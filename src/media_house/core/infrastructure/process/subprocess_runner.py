"""``subprocess``-based adapter for the ProcessRunner port.

This is the *only* module allowed to import ``subprocess`` (enforced by ruff and
by the architecture tests). Security properties:

* ``shell=False`` always; arguments are a list, so there is no shell injection.
* the executable is resolved with ``shutil.which`` before launch;
* stdin is closed (``DEVNULL``) so tools can never block waiting for input;
* output is decoded leniently (``errors="replace"``) - malicious or malformed
  bytes cannot crash the reader.

Known limitation: only the direct child is terminated, not its process tree.
Revisit (process groups / Job Objects) when a wrapped tool spawns grandchildren.
"""

import os
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping

from media_house.core.application.ports.process_runner import (
    OutputLine,
    OutputStream,
    ProcessResult,
    ProcessSpec,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import (
    ExternalSystemError,
    OperationCancelledError,
    ProcessFailedError,
    ProcessTimeoutError,
    ToolNotFoundError,
)
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_POLL_SECONDS = 0.05
_STDERR_TAIL_LINES = 20


class SubprocessRunner:
    def __init__(
        self,
        base_env: Mapping[str, str] | None = None,
        *,
        kill_grace_seconds: float = 3.0,
    ) -> None:
        self._base_env = dict(os.environ if base_env is None else base_env)
        self._kill_grace = kill_grace_seconds

    def run(
        self,
        spec: ProcessSpec,
        *,
        cancellation: CancellationToken | None = None,
        on_output: Callable[[OutputLine], None] | None = None,
    ) -> ProcessResult:
        env = {**self._base_env, **spec.env}
        resolved = shutil.which(spec.executable, path=env.get("PATH"))
        if resolved is None:
            raise ToolNotFoundError(spec.executable)

        started = time.monotonic()
        try:
            process = subprocess.Popen(  # noqa: S603 - argv list, shell=False, resolved executable
                [resolved, *spec.args],
                cwd=spec.cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError as exc:
            raise ExternalSystemError(
                f"Cannot start {spec.executable}: {exc}",
                user_message=f"The tool '{spec.executable}' could not be started.",
                details={"executable": spec.executable},
            ) from exc

        _log.debug("Process started", executable=spec.executable, pid=process.pid)
        stdout_lines: deque[str] = deque(maxlen=spec.max_captured_lines)
        stderr_lines: deque[str] = deque(maxlen=spec.max_captured_lines)
        readers = [
            self._start_reader(process, OutputStream.STDOUT, stdout_lines, on_output),
            self._start_reader(process, OutputStream.STDERR, stderr_lines, on_output),
        ]
        try:
            self._wait(process, spec, started, cancellation)
        finally:
            if process.poll() is None:
                self._terminate(process)
            for reader in readers:
                reader.join(timeout=2.0)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

        result = ProcessResult(
            exit_code=process.returncode,
            stdout="\n".join(stdout_lines),
            stderr="\n".join(stderr_lines),
            duration_seconds=time.monotonic() - started,
        )
        if spec.check and not result.succeeded:
            tail = "\n".join(list(stderr_lines)[-_STDERR_TAIL_LINES:])
            raise ProcessFailedError(spec.executable, result.exit_code, tail)
        return result

    # ------------------------------------------------------------------ #
    def _wait(
        self,
        process: "subprocess.Popen[str]",
        spec: ProcessSpec,
        started: float,
        cancellation: CancellationToken | None,
    ) -> None:
        while True:
            try:
                process.wait(timeout=_POLL_SECONDS)
                return
            except subprocess.TimeoutExpired:
                pass
            if cancellation is not None and cancellation.is_cancelled:
                self._terminate(process)
                raise OperationCancelledError(f"{spec.executable} was cancelled")
            if (
                spec.timeout_seconds is not None
                and time.monotonic() - started > spec.timeout_seconds
            ):
                self._terminate(process)
                raise ProcessTimeoutError(
                    f"{spec.executable} exceeded {spec.timeout_seconds}s",
                    details={"executable": spec.executable, "timeout": spec.timeout_seconds},
                )

    def _terminate(self, process: "subprocess.Popen[str]") -> None:
        process.terminate()
        try:
            process.wait(timeout=self._kill_grace)
        except subprocess.TimeoutExpired:
            _log.warning("Process ignored terminate; killing", pid=process.pid)
            process.kill()
            process.wait()

    @staticmethod
    def _start_reader(
        process: "subprocess.Popen[str]",
        stream_name: OutputStream,
        sink: "deque[str]",
        on_output: Callable[[OutputLine], None] | None,
    ) -> threading.Thread:
        stream = process.stdout if stream_name is OutputStream.STDOUT else process.stderr
        assert stream is not None  # noqa: S101 - PIPE was requested above

        def pump() -> None:
            for raw in stream:
                line = raw.rstrip("\r\n")
                sink.append(line)
                if on_output is not None:
                    try:
                        on_output(OutputLine(stream_name, line))
                    except Exception:  # noqa: BLE001 - a bad observer must not stall the pipe
                        _log.exception("Output callback failed")

        thread = threading.Thread(target=pump, name=f"proc-{stream_name.value}", daemon=True)
        thread.start()
        return thread
