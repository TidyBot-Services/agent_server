"""API routes for code execution."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Query, Request
from pydantic import BaseModel, Field

from fastapi.responses import JSONResponse, Response

from backends.cameras import CameraBackend
from code_executor import CodeExecutor, CodeValidationResult, ExecutionResult, ExecutionStatus
from config import TIMING
from execution_recorder import ExecutionRecorder
from lease import LeaseManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code", tags=["code"])


# Request/Response models
class CodeExecuteRequest(BaseModel):
    """Request to execute code."""
    code: str = Field(..., description="Python code to execute")
    timeout: Optional[float] = Field(None, description="Optional timeout in seconds (default: from lease)")


class CodeExecuteResponse(BaseModel):
    """Response from code execution request."""
    success: bool
    execution_id: str
    message: str = ""
    validation_errors: Optional[list[str]] = None


class CodeStatusResponse(BaseModel):
    """Response with execution status."""
    execution_id: Optional[str]
    status: ExecutionStatus
    is_running: bool
    stdout: str = ""
    stderr: str = ""
    stdout_offset: int = 0
    stderr_offset: int = 0
    duration: float = 0.0
    code: Optional[str] = None
    error: str = ""
    stop_reason: str = ""


class CodeResultResponse(BaseModel):
    """Response with execution result."""
    success: bool
    result: Optional[ExecutionResult]
    error: str = ""


class CodeStopResponse(BaseModel):
    """Response from stop request."""
    success: bool
    message: str


class CodeValidateRequest(BaseModel):
    """Request to validate code without executing."""
    code: str = Field(..., description="Python code to validate")


class CodeValidateResponse(BaseModel):
    """Response from code validation."""
    valid: bool
    errors: list[str] = []
    message: str = ""


# -- Job submit models (fire-and-forget) ------------------------------------

class CodeSubmitRequest(BaseModel):
    """Request to submit code to the job queue (no lease required)."""
    code: str = Field(..., description="Python code to execute")
    holder: str = Field("anonymous", description="Identifier for the submitter")
    timeout: Optional[float] = Field(None, description="Optional timeout in seconds")
    reset_env: bool = Field(True, description="Reset sim environment before running (default: true)")


@dataclass
class Job:
    """A queued code execution job."""
    job_id: str
    code: str
    holder: str
    timeout: float
    reset_env: bool
    status: str = "queued"          # queued | running | completed | failed
    position: int = 0
    submitted_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    execution_id: str = ""
    result: Optional[ExecutionResult] = None
    error: str = ""


class JobQueue:
    """Fire-and-forget job queue that manages lease lifecycle internally."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}           # job_id -> Job
        self._pending: deque[str] = deque()        # job_ids waiting to run
        self._running: bool = False                # is the processor loop active
        self._processor_task: Optional[asyncio.Task] = None
        self._lease_manager: Optional[LeaseManager] = None
        self._camera_backend: Optional[CameraBackend] = None
        self._state_agg = None

    def init(self, lease_manager: LeaseManager, camera_backend: CameraBackend, state_agg=None):
        self._lease_manager = lease_manager
        self._camera_backend = camera_backend
        self._state_agg = state_agg

    def submit(self, code: str, holder: str, timeout: float, reset_env: bool = True) -> Job:
        job_id = str(uuid.uuid4())[:16]
        job = Job(
            job_id=job_id,
            code=code,
            holder=holder,
            timeout=timeout,
            reset_env=reset_env,
            submitted_at=time.time(),
        )
        self._jobs[job_id] = job
        self._pending.append(job_id)
        self._update_positions()
        self._ensure_processor()
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self, holder: Optional[str] = None) -> list[Job]:
        jobs = list(self._jobs.values())
        if holder:
            jobs = [j for j in jobs if j.holder == holder]
        return sorted(jobs, key=lambda j: j.submitted_at, reverse=True)

    def get_summary(self, holder: Optional[str] = None) -> dict:
        jobs = self.list_jobs(holder)
        total = len(jobs)
        by_status = {}
        for j in jobs:
            by_status[j.status] = by_status.get(j.status, 0) + 1
        done = by_status.get("completed", 0) + by_status.get("failed", 0)
        success_rate = by_status.get("completed", 0) / done if done > 0 else None
        return {
            "total": total,
            "completed": by_status.get("completed", 0),
            "failed": by_status.get("failed", 0),
            "queued": by_status.get("queued", 0),
            "running": by_status.get("running", 0),
            "success_rate": success_rate,
        }

    def _update_positions(self):
        for i, job_id in enumerate(self._pending):
            job = self._jobs.get(job_id)
            if job:
                job.position = i + 1

    def _ensure_processor(self):
        if self._processor_task is None or self._processor_task.done():
            self._processor_task = asyncio.create_task(self._process_loop())

    async def _process_loop(self):
        """Process jobs one at a time, acquiring/releasing lease for each."""
        while self._pending:
            job_id = self._pending.popleft()
            job = self._jobs.get(job_id)
            if not job:
                continue
            self._update_positions()
            await self._run_job(job)

    async def _run_job(self, job: Job):
        """Run a single job: acquire lease, execute, release."""
        job.status = "running"
        job.started_at = time.time()
        lease_id = None

        try:
            # Wait for any ongoing reset to finish before acquiring
            for _ in range(120):
                if not self._lease_manager._resetting:
                    break
                await asyncio.sleep(0.5)

            # Acquire lease
            lease_resp = await self._lease_manager.acquire(holder=f"job:{job.holder}:{job.job_id}")
            lease_id = lease_resp.get("lease_id")

            if not lease_id:
                # Lease not available — got a queue ticket, poll until granted
                ticket_id = lease_resp.get("ticket_id")
                if ticket_id:
                    for _ in range(600):  # up to 10 min
                        ticket = self._lease_manager.check_ticket(ticket_id)
                        if ticket.get("status") == "granted":
                            lease_id = ticket.get("lease_id")
                            break
                        if ticket.get("status") in ("expired", "cancelled", "not_found"):
                            break
                        await asyncio.sleep(1)

            if not lease_id:
                job.status = "failed"
                job.error = "Could not acquire lease"
                job.finished_at = time.time()
                return

            # Keep lease alive during execution
            self._lease_manager.record_command()

            # Execute code
            executor = get_executor()
            recorder = get_recorder()
            execution_id = str(uuid.uuid4())[:16]
            job.execution_id = execution_id
            timeout = job.timeout or TIMING.code_execution_timeout_s

            recorder.start(execution_id, self._camera_backend, self._state_agg)
            try:
                result = await executor.execute(
                    code=job.code,
                    execution_id=execution_id,
                    timeout=timeout,
                    lease_id=lease_id,
                    holder=job.holder,
                )
            finally:
                recorder.stop()
                recorder.cleanup_old_recordings()

            job.result = result
            job.status = "completed" if result.status == ExecutionStatus.COMPLETED else "failed"
            if result.error:
                job.error = result.error
            job.finished_at = time.time()
            logger.info(f"Job {job.job_id} finished: {job.status} (execution {execution_id})")

        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.finished_at = time.time()
            logger.error(f"Job {job.job_id} failed: {e}", exc_info=True)

        finally:
            # Release lease
            if lease_id:
                try:
                    await self._lease_manager.release(lease_id)
                except Exception:
                    pass


# Module-level singletons (shared across routes)
_executor: Optional[CodeExecutor] = None
_recorder: Optional[ExecutionRecorder] = None
_job_queue: Optional[JobQueue] = None


def get_executor() -> CodeExecutor:
    """Get or create code executor instance."""
    global _executor
    if _executor is None:
        _executor = CodeExecutor()
    return _executor


def get_recorder() -> ExecutionRecorder:
    """Get or create execution recorder instance."""
    global _recorder
    if _recorder is None:
        _recorder = ExecutionRecorder()
    return _recorder


def get_job_queue() -> JobQueue:
    """Get or create job queue instance."""
    global _job_queue
    if _job_queue is None:
        _job_queue = JobQueue()
    return _job_queue


def init_code_routes(lease_manager: LeaseManager, camera_backend: CameraBackend, state_agg=None):
    """Initialize code routes with dependencies."""

    @router.post("/execute", response_model=CodeExecuteResponse)
    async def execute_code(
        request: Request,
        body: CodeExecuteRequest,
        x_lease_id: Optional[str] = Header(None),
    ):
        """Execute submitted code in subprocess.

        Requires valid lease. Code runs with access to robot_sdk (arm, base, gripper, sensors).

        Returns immediately with execution_id. Use /code/status to check progress.
        """
        # Verify lease
        if not x_lease_id:
            raise HTTPException(status_code=401, detail="Missing X-Lease-Id header")

        if not lease_manager.validate_lease(x_lease_id):
            raise HTTPException(status_code=403, detail="Invalid or expired lease")

        lease_manager.record_command()

        # Check if code is already running
        executor = get_executor()
        if executor.is_running:
            raise HTTPException(
                status_code=409,
                detail="Code is already running. Stop it first with POST /code/stop"
            )

        # Validate code before execution (catches dangerous patterns)
        validation = executor.validate_code(body.code)
        if not validation.valid:
            logger.warning(f"Code validation failed for lease {x_lease_id}: {validation.errors}")
            return CodeExecuteResponse(
                success=False,
                execution_id="",
                message=validation.format_errors(),
                validation_errors=validation.errors,
            )

        # Generate execution ID
        execution_id = str(uuid.uuid4())[:16]

        # Use timeout from request or central default
        timeout = body.timeout if body.timeout is not None else TIMING.code_execution_timeout_s

        # Get holder name from lease, client IP, and API key name
        lease_info = lease_manager.current_lease
        holder = lease_info.holder if lease_info else ""
        client_host = request.client.host if request.client else ""
        api_key_name = getattr(request.state, "auth_user", "")

        logger.info(f"Executing code (ID: {execution_id}) for lease {x_lease_id} holder={holder} from={client_host}")

        # Execute code in background task (non-blocking)
        import asyncio

        recorder = get_recorder()

        async def run_code():
            recorder.start(execution_id, camera_backend, state_agg)
            try:
                result = await executor.execute(
                    code=body.code,
                    execution_id=execution_id,
                    timeout=timeout,
                    lease_id=x_lease_id,  # Pass lease for rewind API
                    holder=holder,
                    client_host=client_host,
                    api_key_name=api_key_name,
                )
                logger.info(f"Code execution {execution_id} finished: {result.status}")
            except Exception as e:
                logger.error(f"Code execution {execution_id} failed: {e}", exc_info=True)
            finally:
                recorder.stop()
                recorder.cleanup_old_recordings()

        # Start execution as background task
        task = asyncio.create_task(run_code())
        request.app.state.background_tasks.add(task)

        return CodeExecuteResponse(
            success=True,
            execution_id=execution_id,
            message=f"Code execution started (ID: {execution_id})",
        )

    @router.post("/validate", response_model=CodeValidateResponse)
    async def validate_code(body: CodeValidateRequest):
        """Validate code without executing it.

        Checks for dangerous patterns (shell commands, network access, file deletion, etc.)
        that trusted lab agents might accidentally include.

        No lease required. Use this to pre-check code before submitting to /execute.
        """
        executor = get_executor()
        validation = executor.validate_code(body.code)

        if validation.valid:
            return CodeValidateResponse(
                valid=True,
                errors=[],
                message="Code validation passed",
            )
        else:
            return CodeValidateResponse(
                valid=False,
                errors=validation.errors,
                message=validation.format_errors(),
            )

    @router.post("/stop", response_model=CodeStopResponse)
    async def stop_code(x_lease_id: Optional[str] = Header(None)):
        """Stop currently running code.

        Requires valid lease. Sends SIGTERM for graceful shutdown.
        """
        # Verify lease
        if not x_lease_id:
            raise HTTPException(status_code=401, detail="Missing X-Lease-Id header")

        if not lease_manager.validate_lease(x_lease_id):
            raise HTTPException(status_code=403, detail="Invalid or expired lease")

        lease_manager.record_command()

        executor = get_executor()
        if not executor.is_running:
            return CodeStopResponse(
                success=False,
                message="No code is currently running",
            )

        logger.info(f"Stopping code execution for lease {x_lease_id}")
        stopped = executor.stop(reason="manual")

        if stopped:
            return CodeStopResponse(
                success=True,
                message="Code execution stopped",
            )
        else:
            return CodeStopResponse(
                success=False,
                message="Failed to stop code execution",
            )

    @router.get("/status", response_model=CodeStatusResponse)
    async def get_status(
        stdout_offset: int = Query(0, ge=0, description="Character offset into stdout; only output after this position is returned"),
        stderr_offset: int = Query(0, ge=0, description="Character offset into stderr; only output after this position is returned"),
    ):
        """Get current execution status.

        Returns execution ID, status, whether code is running, and live output.
        Pass stdout_offset/stderr_offset from the previous response to receive
        only new output since the last poll.
        No lease required (read-only).
        """
        executor = get_executor()
        stdout, stderr = "", ""
        duration = 0.0
        error, stop_reason = "", ""
        if executor.is_running:
            stdout, stderr = executor.get_current_output()
            if executor._start_time:
                duration = time.time() - executor._start_time
        else:
            # Pull final output and stop info from last result
            last = executor.get_last_result()
            if last:
                stdout = last.stdout
                stderr = last.stderr
                duration = last.duration
                error = last.error
                stop_reason = last.stop_reason
        # Slice to return only new output since the requested offsets
        full_stdout_len = len(stdout)
        full_stderr_len = len(stderr)
        stdout = stdout[stdout_offset:]
        stderr = stderr[stderr_offset:]
        return CodeStatusResponse(
            execution_id=executor._execution_id,
            status=executor.status,
            is_running=executor.is_running,
            stdout=stdout,
            stderr=stderr,
            stdout_offset=full_stdout_len,
            stderr_offset=full_stderr_len,
            duration=duration,
            code=executor.current_code,
            error=error,
            stop_reason=stop_reason,
        )

    @router.get("/result", response_model=CodeResultResponse)
    async def get_result():
        """Get result from last execution.

        Returns stdout, stderr, exit code, duration, etc.
        No lease required (read-only).
        """
        executor = get_executor()
        result = executor.get_last_result()

        if result is None:
            return CodeResultResponse(
                success=False,
                result=None,
                error="No execution result available",
            )

        return CodeResultResponse(
            success=True,
            result=result,
            error="",
        )

    @router.get("/history")
    async def get_history(count: int = 3):
        """Get last N execution results (newest first).

        No lease required (read-only).
        """
        executor = get_executor()
        history = executor.get_history(count)
        return {"history": history}

    @router.get("/recordings/{execution_id}")
    async def get_recording(execution_id: str):
        """Get recording metadata with frames matched to nearest state.

        Each entry in the ``timeline`` array pairs a frame with the
        closest state sample by timestamp.  No lease required (read-only).
        """
        import bisect
        from execution_recorder import _CODE_DIR

        recorder = get_recorder()
        metadata = recorder.get_recording(execution_id)
        if metadata is None:
            return JSONResponse(
                {"error": f"No recording found for execution {execution_id}"},
                status_code=404,
            )

        # Load state log timestamps for matching
        state_log_path = _CODE_DIR / execution_id / "state_log.jsonl"
        state_times: list[float] = []
        state_lines: list[dict] = []
        if state_log_path.exists():
            for line in state_log_path.read_text().splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                state_times.append(entry.get("timestamp", 0.0))
                state_lines.append(entry)

        # Build timeline: match each frame to nearest state
        # Timestamps are per capture cycle (one per frame index), but frames
        # list all camera images (multiple per index). Expand timestamps by
        # extracting the frame index from each filename.
        timeline = []
        raw_timestamps = metadata.get("timestamps", [])
        frames = metadata.get("frames", [])
        for frame in frames:
            # Extract frame index from "0023_base_camera.jpg" -> 23
            try:
                fidx = int(frame.split("_", 1)[0])
            except (ValueError, IndexError):
                fidx = 0
            t = raw_timestamps[fidx] if fidx < len(raw_timestamps) else (
                raw_timestamps[-1] if raw_timestamps else 0.0
            )
            matched_state = None
            if state_times:
                idx = bisect.bisect_left(state_times, t)
                # Pick whichever neighbor is closer
                best = None
                if idx < len(state_times):
                    best = idx
                if idx > 0 and (best is None or abs(state_times[idx - 1] - t) <= abs(state_times[best] - t)):
                    best = idx - 1
                if best is not None:
                    matched_state = state_lines[best]
            timeline.append({
                "frame": frame,
                "timestamp": t,
                "state": matched_state,
            })

        return {
            "execution_id": metadata.get("execution_id"),
            "started_at": metadata.get("started_at"),
            "stopped_at": metadata.get("stopped_at"),
            "duration": metadata.get("duration"),
            "cameras": metadata.get("cameras", []),
            "frame_count": metadata.get("frame_count", 0),
            "state_samples": metadata.get("state_samples", 0),
            "frames": [t["frame"] for t in timeline],
            "timeline": timeline,
        }

    @router.get("/recordings/{execution_id}/frames/{filename}")
    async def get_recording_frame(execution_id: str, filename: str):
        """Serve a recorded JPEG frame.

        No lease required (read-only).
        """
        from pathlib import Path

        # Sanitize filename to prevent path traversal
        safe_name = Path(filename).name
        if safe_name != filename or ".." in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        from execution_recorder import _CODE_DIR
        filepath = _CODE_DIR / execution_id / safe_name
        if not filepath.exists() or not filepath.suffix == ".jpg":
            raise HTTPException(status_code=404, detail="Frame not found")

        return Response(
            content=filepath.read_bytes(),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @router.get("/recordings")
    async def list_recordings():
        """List all execution IDs that have recordings (newest first).

        No lease required (read-only).
        """
        recorder = get_recorder()
        return {"recordings": recorder.list_recordings()}

    # -- Fire-and-forget job queue -------------------------------------------

    job_queue = get_job_queue()
    job_queue.init(lease_manager, camera_backend, state_agg)

    @router.post("/submit")
    async def submit_job(body: CodeSubmitRequest):
        """Submit code to the job queue (fire-and-forget).

        No lease required — the server acquires and releases leases internally.
        Jobs are executed in FIFO order. Use GET /code/jobs/{job_id} to check status.
        """
        # Validate code
        executor = get_executor()
        validation = executor.validate_code(body.code)
        if not validation.valid:
            return JSONResponse(
                {"error": "Code validation failed", "details": validation.errors},
                status_code=400,
            )

        timeout = body.timeout if body.timeout is not None else TIMING.code_execution_timeout_s
        job = job_queue.submit(body.code, body.holder, timeout, body.reset_env)
        logger.info(f"Job {job.job_id} submitted by {body.holder} (position {job.position})")

        return {
            "job_id": job.job_id,
            "status": job.status,
            "position": job.position,
        }

    @router.get("/jobs")
    async def list_jobs(holder: Optional[str] = Query(None, description="Filter by holder")):
        """List all jobs with optional holder filter.

        No lease required (read-only).
        """
        jobs = job_queue.list_jobs(holder)
        summary = job_queue.get_summary(holder)

        return {
            "jobs": [
                {
                    "job_id": j.job_id,
                    "holder": j.holder,
                    "status": j.status,
                    "position": j.position if j.status == "queued" else None,
                    "execution_id": j.execution_id or None,
                    "submitted_at": j.submitted_at,
                    "started_at": j.started_at or None,
                    "finished_at": j.finished_at or None,
                    "duration": (j.finished_at - j.started_at) if j.finished_at and j.started_at else None,
                    "exit_code": j.result.exit_code if j.result else None,
                    "error": j.error or None,
                }
                for j in jobs
            ],
            "summary": summary,
        }

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        """Get detailed status of a specific job.

        Returns full result including stdout/stderr when completed.
        No lease required (read-only).
        """
        job = job_queue.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        resp = {
            "job_id": job.job_id,
            "holder": job.holder,
            "status": job.status,
            "position": job.position if job.status == "queued" else None,
            "execution_id": job.execution_id or None,
            "submitted_at": job.submitted_at,
            "started_at": job.started_at or None,
            "finished_at": job.finished_at or None,
            "duration": (job.finished_at - job.started_at) if job.finished_at and job.started_at else None,
            "error": job.error or None,
        }

        if job.result:
            resp["result"] = {
                "status": job.result.status,
                "exit_code": job.result.exit_code,
                "stdout": job.result.stdout,
                "stderr": job.result.stderr,
                "duration": job.result.duration,
                "error": job.result.error,
            }

        return resp

    return router
