import sys
import threading
import time

import pytest

from media_house.core.application.ports import OutputLine, OutputStream, ProcessRunner, ProcessSpec
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import (
    OperationCancelledError,
    ProcessFailedError,
    ProcessTimeoutError,
    ToolNotFoundError,
    ValidationError,
)

pytestmark = pytest.mark.integration


def python(code: str, *args: str, **kwargs: object) -> ProcessSpec:
    return ProcessSpec(sys.executable, ["-c", code, *args], **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def runner() -> ProcessRunner:
    return SubprocessRunner(kill_grace_seconds=1.0)


def test_captures_output_and_exit_code(runner: ProcessRunner) -> None:
    result = runner.run(
        python("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"),
    )
    assert (result.exit_code, result.stdout, result.stderr) == (3, "out", "err")
    assert not result.succeeded
    assert result.duration_seconds >= 0


def test_check_raises_with_stderr_kept_out_of_user_message(runner: ProcessRunner) -> None:
    with pytest.raises(ProcessFailedError) as caught:
        runner.run(
            python("import sys; print('bad codec', file=sys.stderr); sys.exit(7)", check=True)
        )
    assert caught.value.exit_code == 7
    assert caught.value.details["stderr"] == "bad codec"
    assert "bad codec" not in caught.value.user_message


def test_output_is_streamed_line_by_line(runner: ProcessRunner) -> None:
    lines: list[OutputLine] = []
    runner.run(
        python("print('a'); print('b')"),
        on_output=lines.append,
    )
    assert [(line.stream, line.text) for line in lines] == [
        (OutputStream.STDOUT, "a"),
        (OutputStream.STDOUT, "b"),
    ]


def test_timeout_terminates_the_process(runner: ProcessRunner) -> None:
    started = time.monotonic()
    with pytest.raises(ProcessTimeoutError):
        runner.run(python("import time; time.sleep(30)", timeout_seconds=0.3))
    assert time.monotonic() - started < 10


def test_cancellation_terminates_the_process(runner: ProcessRunner) -> None:
    token = CancellationToken()
    threading.Timer(0.3, token.cancel).start()
    started = time.monotonic()
    with pytest.raises(OperationCancelledError):
        runner.run(python("import time; time.sleep(30)"), cancellation=token)
    assert time.monotonic() - started < 10


def test_missing_executable_is_a_typed_error(runner: ProcessRunner) -> None:
    with pytest.raises(ToolNotFoundError) as caught:
        runner.run(ProcessSpec("definitely-not-installed-tool-xyz"))
    assert caught.value.executable == "definitely-not-installed-tool-xyz"


def test_arguments_are_passed_verbatim_never_through_a_shell(runner: ProcessRunner) -> None:
    hostile = "; echo pwned && $(whoami) `id` | cat"
    result = runner.run(python("import sys; print(sys.argv[1])", hostile))
    assert result.stdout == hostile


def test_environment_overrides_are_applied(runner: ProcessRunner) -> None:
    result = runner.run(
        python("import os; print(os.environ['MH_TEST_VALUE'])", env={"MH_TEST_VALUE": "42"}),
    )
    assert result.stdout == "42"


def test_stdin_is_closed_so_tools_cannot_hang_waiting_for_input(runner: ProcessRunner) -> None:
    result = runner.run(python("import sys; print(repr(sys.stdin.read()))", timeout_seconds=10))
    assert result.stdout == "''"


def test_only_the_tail_of_huge_output_is_retained(runner: ProcessRunner) -> None:
    result = runner.run(python("[print(i) for i in range(1000)]", max_captured_lines=5))
    assert result.stdout.splitlines() == ["995", "996", "997", "998", "999"]


def test_a_failing_output_callback_does_not_stall_the_process(runner: ProcessRunner) -> None:
    def bad(_: OutputLine) -> None:
        raise RuntimeError("observer bug")

    result = runner.run(python("print('x')", timeout_seconds=10), on_output=bad)
    assert result.succeeded


@pytest.mark.parametrize(
    ("executable", "args"),
    [("", []), ("   ", []), ("tool", ["bad\x00arg"])],
)
def test_spec_validation(executable: str, args: list[str]) -> None:
    with pytest.raises(ValidationError):
        ProcessSpec(executable, args)
