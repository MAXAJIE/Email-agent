"""Bounded daemon jobs: deadlines do not wait for an uncooperative provider."""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future


class TaskPool:
    def __init__(self, workers: int):
        self.slots = threading.BoundedSemaphore(workers)
        self._lock = threading.Lock()
        self._held: set[int] = set()  # ids of futures still holding a slot

    def _release(self, future: Future) -> None:
        with self._lock:
            if id(future) not in self._held:
                return  # already released
            self._held.discard(id(future))
        self.slots.release()

    def abandon(self, future: Future) -> None:
        """Give back the slot of a call that timed out or was cancelled.

        The worker thread may still be blocked inside the provider's HTTP
        call. Before this existed its slot stayed taken until the provider
        gave up (minutes), so after a few timeouts every new message failed
        with "All agents are busy" - the "keeps telling error" symptom.
        """
        self._release(future)
        self._lock = threading.Lock()
        self._held: set[int] = set()  # ids of futures still holding a slot

    def _release(self, future: Future) -> None:
        with self._lock:
            if id(future) not in self._held:
                return  # already released
            self._held.discard(id(future))
        self.slots.release()

    def abandon(self, future: Future) -> None:
        """Give back the slot of a call that timed out or was cancelled.

        The worker thread may still be blocked inside the provider's HTTP
        call. Before this existed its slot stayed taken until the provider
        gave up (minutes), so after a few timeouts every new message failed
        with "All agents are busy" - the "keeps telling error" symptom.
        """
        self._release(future)

    def submit(self, function, *args) -> Future:
        # No unbounded executor queue. Running abandoned calls retain capacity.
        if not self.slots.acquire(blocking=False):
            raise RuntimeError("All agents are busy; wait for current calls to finish.")
        future = Future()
        with self._lock:
            self._held.add(id(future))

        def work():
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(function(*args))
                    except BaseException as exc:
                        future.set_exception(exc)
            finally:
                self._release(future)

        threading.Thread(target=work, name="mailkeep-worker", daemon=True).start()
        return future


class ChatJobs:
    """One mailbox turn at a time, idempotent submission and retained results."""
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        self.pool = TaskPool(1)

    def submit(self, agent, text: str, request_id: str) -> dict:
        if not request_id or len(request_id) > 128:
            raise ValueError("A request_id of 1-128 characters is required")
        with self.lock:
            old = self.jobs.get(request_id)
            if old:
                if old["text"] != text or old["agent"] is not agent:
                    raise ValueError("request_id already belongs to a different request")
                return {"job_id": request_id}
            # Keep terminal results for an hour. Never expire a running job.
            now = time.monotonic()
            self.jobs = {k: v for k, v in self.jobs.items()
                         if not v["future"].done() or now - v["created"] < 3600}
            if len(self.jobs) >= 100:
                raise RuntimeError("Task history is full; restart the local app after current work finishes")
            job = {"agent": agent, "text": text, "created": now, "steps": []}
            self.jobs[request_id] = job

            def run():
                def note(msg):
                    self.add_step(request_id, msg)
                try:
                    return agent.chat(text, note)
                except TypeError:
                    return agent.chat(text)  # test doubles without step support

            try:
                job["future"] = self.pool.submit(run)
            except Exception:
                del self.jobs[request_id]
                raise
            return {"job_id": request_id}

    def add_step(self, job_id: str, text: str) -> None:
        """Record a thinking step for the UI; best-effort, never raises."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None and len(job["steps"]) < 50:
                job["steps"].append(str(text)[:120])

    def status(self, job_id: str) -> dict:
        from .web import outcome_json
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            future = job.get("future")
            steps = list(job["steps"])
        if future is None or not future.done():
            return {"state": "running", "steps": steps}
        try:
            return {"state": "completed", "steps": steps, "outcome": outcome_json(future.result())}
        except Exception:
            return {"state": "failed", "steps": steps,
                    "error": "The task failed. Check the local console; no request was retried."}

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job["future"].cancel() or job["agent"].cancel()
