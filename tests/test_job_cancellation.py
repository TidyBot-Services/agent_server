"""Queue cancellation must target exactly the submitted job."""

import asyncio

from routes import code_routes


def test_queued_job_cancelled_without_running(monkeypatch):
    queue = code_routes.JobQueue()
    monkeypatch.setattr(queue, "_ensure_processor", lambda: None)
    job = queue.submit("print('never run')", "owner", 10)

    result = asyncio.run(queue.cancel(job))

    assert result == {"job_id": job.job_id, "status": "cancelled", "cancelled": True}
    assert job.job_id not in queue._pending
    assert job.finished_at > 0


def test_running_job_stops_only_matching_execution(monkeypatch):
    class Executor:
        is_running = True
        _execution_id = "another-job"

        def __init__(self):
            self.stop_reasons = []

        def stop(self, reason):
            self.stop_reasons.append(reason)
            return True

    executor = Executor()
    monkeypatch.setattr(code_routes, "get_executor", lambda: executor)
    queue = code_routes.JobQueue()
    job = code_routes.Job("job-1", "pass", "owner", 10, False,
                          status="running", execution_id="this-job")

    asyncio.run(queue.cancel(job))
    assert job.cancel_requested is True
    assert executor.stop_reasons == []

    executor._execution_id = "this-job"
    asyncio.run(queue.cancel(job))
    assert executor.stop_reasons == ["job_cancelled"]
