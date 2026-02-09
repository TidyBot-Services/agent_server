"""API routes for code execution."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field

from code_executor import CodeExecutor, CodeValidationResult, ExecutionResult, ExecutionStatus
from config import TIMING
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
    duration: float = 0.0
    code: Optional[str] = None


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


# Module-level code executor instance (shared across routes)
_executor: Optional[CodeExecutor] = None


def get_executor() -> CodeExecutor:
    """Get or create code executor instance."""
    global _executor
    if _executor is None:
        _executor = CodeExecutor()
    return _executor


def init_code_routes(lease_manager: LeaseManager):
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
        execution_id = str(uuid.uuid4())[:8]

        # Use timeout from request or central default
        timeout = body.timeout if body.timeout is not None else TIMING.code_execution_timeout_s

        # Get holder name from lease and client IP
        lease_info = lease_manager.current_lease
        holder = lease_info.holder if lease_info else ""
        client_host = request.client.host if request.client else ""

        logger.info(f"Executing code (ID: {execution_id}) for lease {x_lease_id} holder={holder} from={client_host}")

        # Execute code in background task (non-blocking)
        import asyncio

        async def run_code():
            try:
                result = await executor.execute(
                    code=body.code,
                    execution_id=execution_id,
                    timeout=timeout,
                    lease_id=x_lease_id,  # Pass lease for rewind API
                    holder=holder,
                    client_host=client_host,
                )
                logger.info(f"Code execution {execution_id} finished: {result.status}")
            except Exception as e:
                logger.error(f"Code execution {execution_id} failed: {e}", exc_info=True)

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
    async def get_status():
        """Get current execution status.

        Returns execution ID, status, whether code is running, and live output.
        No lease required (read-only).
        """
        executor = get_executor()
        stdout, stderr = "", ""
        duration = 0.0
        if executor.is_running:
            stdout, stderr = executor.get_current_output()
            if executor._start_time:
                duration = time.time() - executor._start_time
        return CodeStatusResponse(
            execution_id=executor._execution_id,
            status=executor.status,
            is_running=executor.is_running,
            stdout=stdout,
            stderr=stderr,
            duration=duration,
            code=executor.current_code,
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

    return router
