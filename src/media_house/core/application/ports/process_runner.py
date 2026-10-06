"""Port for running external programs (FFmpeg, FFprobe, CLI tools, ...).

Contract
--------
* ``run`` never uses a shell. The command is an executable plus an argument
  *list*; arguments are passed verbatim.
* Failure is reported with typed errors:
  ``ToolNotFoundError``, ``ProcessTimeoutError``, ``ProcessFailedError``
  (only when ``check=True``), ``OperationCancelledError`` (cancellation).
* Output can be streamed line by line through ``on_output`` while the process runs.
* On timeout or cancellation the process is terminated before the error is raised.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ValidationError


class OutputStream(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True, slots=True)
class OutputLine:
    stream: OutputStream
    text: str


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    executable: str
    args: Sequence[str] = ()
    cwd: Path | None = None
    #: Variables added to (overriding) the runner's base environment.
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    #: If true, a non-zero exit code raises ``ProcessFailedError``.
    check: bool = False
    #: Only the last N lines of each stream are kept in the result (memory bound).
    max_captured_lines: int = 10_000

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise ValidationError("Executable must not be empty", field="executable")
        if any("\x00" in part for part in (self.executable, *self.args)):
            raise ValidationError("Command contains a NUL character", field="args")


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


class ProcessRunner(Protocol):
    def run(
        self,
        spec: ProcessSpec,
        *,
        cancellation: CancellationToken | None = None,
        on_output: Callable[[OutputLine], None] | None = None,
    ) -> ProcessResult: ...
