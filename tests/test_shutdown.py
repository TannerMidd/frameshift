"""Background services release sockets, threads, and database handles."""

import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import _shutdown_runtime
from elite.journal import JournalWatcher
from elite.state import AppState
from elite.eddn import EddnListener
from elite.extensions import ExtensionManager
from elite.server import ServerThread


class FakeAgain(Exception):
    pass


class FakeConnection:
    def __init__(self):
        self.closed = threading.Event()

    def close(self):
        self.closed.set()


class FakeSocket:
    def __init__(self):
        self.receiving = threading.Event()
        self.closed = threading.Event()

    def setsockopt(self, *_args):
        pass

    def connect(self, _endpoint):
        pass

    def recv(self):
        self.receiving.set()
        time.sleep(0.01)
        raise FakeAgain()

    def close(self, linger=0):
        assert linger == 0
        self.closed.set()


class FakeContext:
    def __init__(self, socket):
        self._socket = socket
        self.terminated = threading.Event()

    def socket(self, _kind):
        return self._socket

    def term(self):
        assert self._socket.closed.is_set()
        self.terminated.set()


def test_eddn_shutdown():
    import elite.eddn as eddn

    fake_socket = FakeSocket()
    fake_context = FakeContext(fake_socket)
    fake_zmq = SimpleNamespace(
        SUB=1,
        SUBSCRIBE=2,
        RCVTIMEO=3,
        Again=FakeAgain,
        Context=lambda: fake_context,
    )
    connection = FakeConnection()
    connect_calls = []
    original_zmq = sys.modules.get("zmq")
    original_connect = eddn.marketdb.connect
    sys.modules["zmq"] = fake_zmq
    eddn.marketdb.connect = lambda: connect_calls.append(True) or connection
    listener = EddnListener()
    try:
        listener.pause_db(timeout=0)
        worker = listener.start()
        assert listener.start() is worker  # starting twice never leaks a worker
        assert listener._db_released.wait(1), "pre-open pause was not acknowledged"
        assert not connect_calls, "listener opened SQLite while a swap was paused"
        listener.resume_db()
        assert fake_socket.receiving.wait(1), "subscriber did not begin receiving"
        assert listener.stop(timeout=1), "subscriber did not stop within its bound"
        assert not worker.is_alive()
        assert connection.closed.is_set(), "SQLite connection remained open"
        assert fake_socket.closed.is_set(), "ZeroMQ socket remained open"
        assert fake_context.terminated.is_set(), "owned ZeroMQ context remained open"
        assert listener.stop(timeout=0.01)  # idempotent
    finally:
        listener.stop(timeout=1)
        eddn.marketdb.connect = original_connect
        if original_zmq is None:
            sys.modules.pop("zmq", None)
        else:
            sys.modules["zmq"] = original_zmq


def test_http_shutdown():
    server = ServerThread(AppState(), host="127.0.0.1", port=0)
    worker = server.start()
    assert server.running()
    server.shutdown()
    assert not worker.is_alive()
    assert not server.running()
    assert server._server.socket.fileno() == -1
    server.shutdown()  # idempotent

    # BaseServer.shutdown() blocks forever if called before serve_forever().
    # Our wrapper must close this partially-started resource without calling it.
    unopened = ServerThread(AppState(), host="127.0.0.1", port=0)
    unopened.shutdown()
    assert unopened._server.socket.fileno() == -1


def test_extension_executor_shutdown():
    manager = ExtensionManager()
    executor = manager._executor
    started = threading.Event()
    release = threading.Event()

    def work():
        started.set()
        release.wait(1)

    executor.submit(work)
    assert started.wait(1)
    release.set()
    manager.shutdown(wait=True)
    assert all(not worker.is_alive() for worker in executor._threads)
    manager.shutdown(wait=True)  # idempotent


def test_cleanup_order_and_fault_isolation():
    import app

    calls = []

    class Component:
        def __init__(self, name, fail=False):
            self.name = name
            self.fail = fail

        def shutdown(self, **_kwargs):
            calls.append(self.name)
            if self.fail:
                raise RuntimeError("expected cleanup failure")

        def stop(self, **_kwargs):
            return self.shutdown()

    original_logging_shutdown = app.logging.shutdown
    original_get_logger = app.logging.getLogger
    app.logging.shutdown = lambda: calls.append("logging")
    app.logging.getLogger = lambda *_args, **_kwargs: SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        exception=lambda *_args, **_kwargs: None,
    )
    try:
        _shutdown_runtime(
            Component("http", fail=True),
            Component("journal"),
            Component("eddn"),
            Component("extensions"),
        )
    finally:
        app.logging.shutdown = original_logging_shutdown
        app.logging.getLogger = original_get_logger
    assert calls == ["http", "journal", "eddn", "extensions", "logging"], calls


def test_journal_watcher_shutdown():
    with tempfile.TemporaryDirectory() as temp:
        watcher = JournalWatcher(AppState(), journal_dir=temp)
        worker = watcher.start()
        assert watcher.start() is worker
        assert watcher.stop(timeout=2)
        assert not worker.is_alive()
        assert watcher.stop(timeout=2)  # idempotent


test_eddn_shutdown()
test_http_shutdown()
test_extension_executor_shutdown()
test_journal_watcher_shutdown()
test_cleanup_order_and_fault_isolation()
print("shutdown OK: EDDN/SQLite/ZMQ, HTTP, extensions, logging")
