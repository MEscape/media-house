"""Attaching the final mix to a rendered picture: video copied, sound replaced, length checked."""

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from media_house.modules.audio_mix_engine.application.attach_mix import (
    AttachMix,
    AttachMixCommand,
)
from media_house.modules.audio_mix_engine.domain.errors import BackendFailed
from media_house.modules.media_library.application.dto import DerivedAssetResultDto
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.support.asset_dto import make_asset
from tests.support.media_factories import ffmpeg
from tests.unit.audio_mix_engine.audio_support import CANCEL, backend, tone


def _picture(path: Path, seconds: int = 2) -> Path:
    ffmpeg(
        "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=25",
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path


def _probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],  # noqa: S607
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


class _Library:
    def __init__(self, files: dict[str, Path]) -> None:
        self.files = files
        self.registered: list[tuple[str, Path, dict[str, Any]]] = []
        self.kept: Path | None = None

    def get(self, asset_id: str) -> Any:
        return Ok(make_asset(asset_id))

    def local_path(self, asset_id: str) -> Any:
        return Ok(self.files[asset_id])

    def register_derived(self, source: str, path: Path, **kw: Any) -> Any:
        self.kept = path.with_name("kept_" + path.name)
        self.kept.write_bytes(path.read_bytes())  # the temporary directory goes away
        self.registered.append((source, self.kept, kw))
        return Ok(DerivedAssetResultDto(make_asset("delivery"), created=True))


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root

    @contextmanager
    def temporary_directory(self, prefix: str = "") -> Any:
        d = self.root / "tmp"
        d.mkdir(exist_ok=True)
        yield d


def _use_case(tmp_path: Path, mix: Path, picture: Path) -> tuple[AttachMix, _Library]:
    lib = _Library({"pic": picture, "mix": mix})
    return AttachMix(lib, backend(), _Paths(tmp_path)), lib  # type: ignore[arg-type]


def test_the_backend_copies_the_video_and_replaces_the_sound(tmp_path: Path) -> None:
    pic = _picture(tmp_path / "pic.mp4")
    mix = tone(tmp_path / "mix.wav", 2.0, hz=880)
    out = tmp_path / "out.mp4"

    backend().attach_audio(pic, mix, out, CANCEL)

    streams = _probe(out)
    assert streams["video"]["codec_name"] == "h264" and streams["audio"]["codec_name"] == "aac"
    assert streams["audio"]["sample_rate"] == "48000"


def test_the_delivered_file_is_registered_as_a_derivative_and_measured(tmp_path: Path) -> None:
    pic = _picture(tmp_path / "pic.mp4")
    mix = tone(tmp_path / "mix.wav", 2.0, hz=880)
    use_case, lib = _use_case(tmp_path, mix, pic)

    result = use_case.execute(AttachMixCommand("pic", "mix"), JobContext.detached())

    assert isinstance(result, Ok)
    [(source, _, kw)] = lib.registered
    assert source == "pic" and kw["operation"] == "delivery_with_mix"
    assert kw["config"] == {"mix_asset_id": "mix"}
    assert result.value.measurement.duration_samples > 0 and result.value.created


def test_a_mix_of_another_length_is_refused_not_attached(tmp_path: Path) -> None:
    pic = _picture(tmp_path / "pic.mp4", seconds=2)
    mix = tone(tmp_path / "mix.wav", 3.0)
    use_case, lib = _use_case(tmp_path, mix, pic)

    result = use_case.execute(AttachMixCommand("pic", "mix"), JobContext.detached())

    assert isinstance(result, Err) and isinstance(result.error, BackendFailed)
    assert "drift" in result.error.user_message and lib.registered == []
