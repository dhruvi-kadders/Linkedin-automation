from __future__ import annotations

import multiprocessing
import queue
import threading
from dataclasses import dataclass
from typing import Any

from .config import MAX_CONCURRENT_WORKERS
from .events import event_broker


@dataclass
class WorkerHandle:
    account_id: str
    process: multiprocessing.Process
    event_queue: Any
    monitor_thread: threading.Thread


class WorkerManager:
    def __init__(self) -> None:
        self._ctx = multiprocessing.get_context("spawn")
        self._workers: dict[str, WorkerHandle] = {}
        self._lock = threading.RLock()
        self.max_concurrent = MAX_CONCURRENT_WORKERS

    def _cleanup(self, account_id: str, process: multiprocessing.Process) -> None:
        with self._lock:
            handle = self._workers.get(account_id)
            if handle and handle.process.pid == process.pid:
                self._workers.pop(account_id, None)

    def _monitor_worker(self, account_id: str, process: multiprocessing.Process, event_queue: Any) -> None:
        while True:
            try:
                message = event_queue.get(timeout=0.5)
                if isinstance(message, dict):
                    event_broker.publish(message)
            except queue.Empty:
                if not process.is_alive():
                    break
            except Exception:
                break

        while True:
            try:
                message = event_queue.get_nowait()
                if isinstance(message, dict):
                    event_broker.publish(message)
            except queue.Empty:
                break
            except Exception:
                break

        process.join(timeout=0.1)
        self._cleanup(account_id, process)
        event_broker.publish(
            {
                "type": "exit",
                "accountId": account_id,
                "payload": {"code": process.exitcode},
            }
        )

    def start(self, account_id: str, options: dict[str, Any] | None = None) -> multiprocessing.Process:
        options = options or {}
        prioritized_application_id = options.get("prioritizedApplicationId")

        with self._lock:
            if account_id in self._workers and self._workers[account_id].process.is_alive():
                raise RuntimeError(f"Worker for account {account_id} is already running")
            if len(self.get_running()) >= self.max_concurrent:
                raise RuntimeError(f"Max concurrent workers ({self.max_concurrent}) reached")

        from .bot.worker_process import run_worker_process

        event_queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=run_worker_process,
            args=(account_id, prioritized_application_id, event_queue),
            daemon=True,
        )
        process.start()

        monitor_thread = threading.Thread(
            target=self._monitor_worker,
            args=(account_id, process, event_queue),
            daemon=True,
        )
        monitor_thread.start()

        with self._lock:
            self._workers[account_id] = WorkerHandle(
                account_id=account_id,
                process=process,
                event_queue=event_queue,
                monitor_thread=monitor_thread,
            )

        return process

    def start_many(self, account_ids: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for account_id in account_ids:
            try:
                self.start(account_id)
                results.append({"accountId": account_id, "started": True})
            except Exception as err:
                results.append({"accountId": account_id, "started": False, "error": str(err)})
        return results

    def stop(self, account_id: str) -> bool:
        with self._lock:
            handle = self._workers.pop(account_id, None)

        if not handle:
            return False

        if handle.process.is_alive():
            handle.process.terminate()
            handle.process.join(timeout=5)

        event_broker.publish(
            {
                "type": "status",
                "accountId": account_id,
                "payload": {"status": "idle", "message": "Worker stopped"},
            }
        )
        return True

    def stop_all(self) -> None:
        account_ids = self.get_running()
        for account_id in account_ids:
            self.stop(account_id)

    def get_running(self) -> list[str]:
        running: list[str] = []
        stale: list[str] = []

        with self._lock:
            items = list(self._workers.items())

        for account_id, handle in items:
            if handle.process.is_alive():
                running.append(account_id)
            else:
                stale.append(account_id)

        if stale:
            with self._lock:
                for account_id in stale:
                    self._workers.pop(account_id, None)

        return running

    def is_running(self, account_id: str) -> bool:
        with self._lock:
            handle = self._workers.get(account_id)
        return bool(handle and handle.process.is_alive())


worker_manager = WorkerManager()
