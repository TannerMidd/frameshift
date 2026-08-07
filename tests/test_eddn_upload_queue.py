"""Bounded EDDN upload concurrency, backpressure, deduplication and shutdown."""

import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite.eddn_upload import EddnUploader  # noqa: E402


def test_burst_is_bounded_and_dropped_key_can_retry():
    uploader = EddnUploader(worker_count=2, queue_size=3)
    release = threading.Event()
    both_started = threading.Event()
    capture_lock = threading.Lock()
    started = []
    completed = []

    def publish(value):
        with capture_lock:
            started.append(value)
            if len(started) == 2:
                both_started.set()
        assert release.wait(5), "test did not release blocked upload workers"
        with capture_lock:
            completed.append(value)

    try:
        assert uploader._enqueue(("event", 0), publish, (0,))
        assert uploader._enqueue(("event", 1), publish, (1,))
        assert both_started.wait(2)

        # The two workers are occupied, so exactly three more observations fit
        # in the bounded waiting queue. No additional thread is created.
        for value in (2, 3, 4):
            assert uploader._enqueue(("event", value), publish, (value,))
        assert not uploader._enqueue(("event", 2), publish, (2,))
        assert not uploader._enqueue(("event", 5), publish, (5,))

        stats = uploader.stats()
        assert stats["queued"] == 3
        assert stats["in_flight"] == 2
        assert stats["dropped"] == 1
        assert len([worker for worker in uploader._workers if worker.is_alive()]) == 2

        release.set()
        assert uploader.drain(timeout=2)

        # A queue-full drop must not update the deduplication cursor. The same
        # observation remains eligible once capacity is available.
        assert uploader._enqueue(("event", 5), publish, (5,))
        assert uploader.drain(timeout=2)
        assert sorted(completed) == [0, 1, 2, 3, 4, 5]
    finally:
        release.set()
        assert uploader.shutdown(timeout=2, drain=False)


def test_shutdown_drains_and_is_idempotent():
    uploader = EddnUploader(worker_count=1, queue_size=8)
    capture_lock = threading.Lock()
    completed = []

    def publish(value):
        with capture_lock:
            completed.append(value)

    for value in range(6):
        assert uploader._enqueue(("event", value), publish, (value,))

    assert uploader.shutdown(timeout=2)
    assert sorted(completed) == list(range(6))
    assert uploader.stats()["accepting"] is False
    assert not uploader._enqueue(("event", 99), publish, (99,))
    assert uploader.shutdown(timeout=2)
    assert not any(worker.is_alive() for worker in uploader._workers)


def test_shutdown_timeout_can_be_awaited_again():
    uploader = EddnUploader(worker_count=1, queue_size=1)
    started = threading.Event()
    release = threading.Event()

    def publish():
        started.set()
        assert release.wait(5), "test did not release blocked upload worker"

    assert uploader._enqueue(("event", 1), publish, ())
    assert started.wait(2)
    assert uploader.shutdown(timeout=0.01) is False
    assert uploader.stats()["accepting"] is False
    release.set()
    assert uploader.shutdown(timeout=2)


test_burst_is_bounded_and_dropped_key_can_retry()
test_shutdown_drains_and_is_idempotent()
test_shutdown_timeout_can_be_awaited_again()

print("EDDN upload queue OK: bounded burst, retry-safe backpressure and shutdown drain")
