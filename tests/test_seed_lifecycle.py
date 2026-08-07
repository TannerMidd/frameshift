"""Seeder lifecycle transitions are atomic under concurrent HTTP starts."""

import sys
import threading
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elite.seed import Seeder  # noqa: E402


def test_concurrent_starts_launch_one_worker():
    release_worker = threading.Event()
    worker_started = threading.Event()
    launch_callers = threading.Barrier(3)
    observe_liveness = threading.Barrier(2)
    results = []
    workers = []
    workers_lock = threading.Lock()

    class CoordinatedSeeder(Seeder):
        def running(self):
            # Deterministically expose the former check-then-start race: both
            # callers observe the same stopped worker before either may leave
            # running(). Atomic start implementations need not call this
            # public, independently synchronized status method.
            result = super().running()
            observe_liveness.wait(timeout=2)
            return result

        def _run(self, _include_carriers, _keep_dump):
            with workers_lock:
                workers.append(threading.current_thread())
                worker_started.set()
            release_worker.wait()

    seeder = CoordinatedSeeder()

    def start_seed():
        launch_callers.wait(timeout=2)
        results.append(seeder.start())

    callers = [threading.Thread(target=start_seed) for _ in range(2)]
    try:
        for caller in callers:
            caller.start()
        launch_callers.wait(timeout=2)
        for caller in callers:
            caller.join(timeout=2)
        assert all(not caller.is_alive() for caller in callers), "start callers deadlocked"
        assert worker_started.wait(2), "seed worker did not start"
        assert sorted(results) == [False, True], results
        assert len(workers) == 1, workers
    finally:
        release_worker.set()
        for worker in workers:
            worker.join(timeout=2)


test_concurrent_starts_launch_one_worker()
print("seed lifecycle OK: concurrent starts launch exactly one worker")
