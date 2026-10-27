"""The scratch runner: arguments, exit codes, dry run, JSON report, cancellation, reuse.

The script is loaded by path and called in-process; real FFmpeg, library and modules.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from media_house.shared.concurrency import CancellationToken
from tests.support.proxy_media import cfr_with_audio

pytestmark = pytest.mark.integration

SCRIPT = Path(__file__).resolve().parents[3] / "scratch" / "run_pipeline.py"
CHEAP = "inspect,thumbnail"


@pytest.fixture(scope="module")
def cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_pipeline_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(cli: ModuleType, tmp_path: Path, *args: str) -> int:
    code: int = cli.main([*args, "--home", str(tmp_path / "home")])
    return code


def sha(path: Path) -> bytes:
    return path.read_bytes()


# --- arguments --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--input", "x.mp4", "--all", "--stages", "inspect"],
        ["--input", "x.mp4", "--stages", "inspect,nonsense"],
        ["--input", "x.mp4", "--gpu-slots", "0"],
        ["--input", "x.mp4", "--cpu-slots", "many"],
    ],
)
def test_invalid_arguments_exit_2_before_anything_starts(
    cli: ModuleType, tmp_path: Path, args: list[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main([*args, "--home", str(tmp_path / "home")])

    assert stopped.value.code == 2
    assert not (tmp_path / "home").exists()  # the application was never started


def test_unknown_keys_and_broken_files_in_the_config_are_rejected(
    cli: ModuleType, tmp_path: Path
) -> None:
    typo = tmp_path / "typo.toml"
    typo.write_text('stagez = ["inspect"]\n')
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")

    for config in (typo, broken, tmp_path / "missing.toml"):
        with pytest.raises(SystemExit) as stopped:
            cli.main(["--input", "x.mp4", "--config", str(config)])
        assert stopped.value.code == 2


def test_a_missing_input_is_reported_and_exits_1(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clip = cfr_with_audio(tmp_path / "ok.mp4", seconds=1)

    code = run(
        cli,
        tmp_path,
        "--input",
        str(clip),
        "--input",
        str(tmp_path / "gone.mp4"),
        "--stages",
        "inspect",
    )

    assert code == 1
    assert "gone.mp4: does not exist" in capsys.readouterr().err


# --- dry run ------------------------------------------------------------------------------------
def test_dry_run_imports_nothing_runs_nothing_and_leaves_the_source_alone(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clip = cfr_with_audio(tmp_path / "clip.mp4", seconds=1)
    before = sha(clip)
    report = tmp_path / "out" / "report.json"

    code = run(
        cli, tmp_path, "--input", str(clip), "--all", "--dry-run", "--report-json", str(report)
    )

    data = json.loads(report.read_text())
    assert code == 0
    assert data["dry_run"] is True
    assert data["assets"][0]["origin"] == "would_import"
    assert {t["status"] for t in data["assets"][0]["tasks"]} == {None}
    assert data["levels"]
    assert sha(clip) == before
    out = capsys.readouterr().out
    assert "Execution graph" in out and "> start" not in out
    # nothing was imported: a real run now reports the file as freshly imported
    code = run(cli, tmp_path, "--input", str(clip), "--stages", CHEAP, "--report-json", str(report))
    assert json.loads(report.read_text())["assets"][0]["origin"] == "imported"
    assert code == 0


# --- reports and reuse --------------------------------------------------------------------------
def test_report_is_valid_deterministic_json_written_atomically(
    cli: ModuleType, tmp_path: Path
) -> None:
    clip = cfr_with_audio(tmp_path / "clip.mp4", seconds=1)
    out = tmp_path / "out"

    code = run(cli, tmp_path, "--input", str(clip), "--stages", CHEAP, "--output", str(out))

    text = (out / "report.json").read_text()
    data = json.loads(text)
    assert code == 0 and data["ok"] is True and data["schema_version"] == 1
    assert list(out.iterdir()) == [out / "report.json"]  # no temporary file left behind
    assert text == json.dumps(data, indent=2, sort_keys=True)
    cold = {t["kind"]: t for t in data["assets"][0]["tasks"] if t["status"] == "succeeded"}
    assert set(cold) == {"inspect", "thumbnail"}
    assert {t["cache"] for t in cold.values()} == {"miss"}


def test_second_run_is_served_before_queueing_and_never_recomputed(
    cli: ModuleType, tmp_path: Path
) -> None:
    clip = cfr_with_audio(tmp_path / "clip.mp4", seconds=1)
    out = tmp_path / "out"
    run(cli, tmp_path, "--input", str(clip), "--stages", CHEAP, "--output", str(out))

    code = run(cli, tmp_path, "--input", str(clip), "--stages", CHEAP, "--output", str(out))

    data = json.loads((out / "report.json").read_text())
    tasks = [t for t in data["assets"][0]["tasks"] if t["status"] not in ("skipped_disabled", None)]
    assert code == 0
    assert {t["status"] for t in tasks} == {"reused"}
    assert {t["cache"] for t in tasks} == {"preflight_hit"}
    assert data["cache"] == {"preflight_hit": len(tasks)}
    assert data["peak_running"]["cpu"] == 0 and data["peak_running"]["light"] == 0  # no slot taken


def test_config_file_sets_stages_and_the_command_line_wins(cli: ModuleType, tmp_path: Path) -> None:
    clip = cfr_with_audio(tmp_path / "clip.mp4", seconds=1)
    config = tmp_path / "pipeline.toml"
    config.write_text('stages = ["inspect"]\ngpu_slots = 1\n')
    out = tmp_path / "out"

    run(cli, tmp_path, "--input", str(clip), "--config", str(config), "--output", str(out))
    from_config = json.loads((out / "report.json").read_text())
    run(
        cli,
        tmp_path,
        "--input",
        str(clip),
        "--config",
        str(config),
        "--stages",
        CHEAP,
        "--output",
        str(out),
    )
    from_cli = json.loads((out / "report.json").read_text())

    def requested(data: dict[str, object]) -> set[str]:
        tasks = data["assets"][0]["tasks"]  # type: ignore[index]
        return {t["kind"] for t in tasks if t["status"] != "skipped_disabled"}

    assert requested(from_config) == {"inspect"}
    assert requested(from_cli) == {"inspect", "thumbnail"}


def test_source_files_are_byte_identical_after_a_run(cli: ModuleType, tmp_path: Path) -> None:
    clip = cfr_with_audio(tmp_path / "clip.mp4", seconds=1)
    before = sha(clip)

    run(cli, tmp_path, "--input", str(clip), "--stages", "inspect,thumbnail,proxy")

    assert sha(clip) == before


# --- interruption -------------------------------------------------------------------------------
def test_an_interrupted_run_reports_cancelled_and_exits_130(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = cfr_with_audio(tmp_path / "clip.mp4", seconds=1)
    out = tmp_path / "out"

    def interrupt_at_once(token: CancellationToken) -> None:
        token.cancel()  # as if Ctrl+C arrived before the first task

    monkeypatch.setattr(cli, "install_interrupt", interrupt_at_once)

    code = run(cli, tmp_path, "--input", str(clip), "--stages", CHEAP, "--output", str(out))

    data = json.loads((out / "report.json").read_text())
    assert code == 130
    assert data["cancelled"] is True and data["ok"] is False
