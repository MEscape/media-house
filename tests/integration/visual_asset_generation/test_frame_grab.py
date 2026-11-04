"""Frame grabs with the real FFmpeg: exact picture, 16-bit PNG, derived from the video, cut out."""

import shutil
from pathlib import Path

import pytest

from media_house.core.domain import Rational
from media_house.modules.media_inspection.application.contracts import (
    InspectMediaCommand,
    MediaInspector,
)
from media_house.modules.visual_asset_generation.application.contracts import (
    FRAME_GRAB_OPERATION,
    AssetKind,
    AssetRequest,
    FrameGrabber,
    FrameGrabFailed,
    GrabFrameCommand,
    InputHow,
    ItemStatus,
    PrepareVisualAssetsCommand,
    ResultState,
    VariantName,
)
from media_house.modules.visual_asset_generation.application.grab_frame import FrameGrabResult
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.integration.visual_asset_generation.conftest import Lab
from tests.support.media_factories import ffmpeg

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg/ffprobe are not on PATH",
    ),
]

EXACT = ["-vf", "scale=flags=accurate_rnd+full_chroma_int+full_chroma_inp", "-pix_fmt", "rgb48be"]


def import_video(lab: Lab, path: Path, *, inspect: bool) -> str:
    imported = lab.library.import_file(path)
    assert isinstance(imported, Ok), imported
    video_id = imported.value.asset.id
    if inspect:
        inspected = lab.resolve(MediaInspector).execute(
            InspectMediaCommand(video_id), JobContext.detached()
        )
        assert isinstance(inspected, Ok), inspected
    return video_id


def moving_pattern(lab: Lab) -> Path:
    path = lab.tmp / "pattern.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=duration=2:size=160x120:rate=25", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", str(path))  # fmt: skip
    return path


def reference_frame(lab: Lab, video: Path, index: int) -> bytes:
    """Picture ``index`` decoded from the start (no seeking at all), same conversion."""
    out = lab.tmp / f"ref{index}.png"
    ffmpeg("-i", str(video), "-vf",
           f"select=eq(n\\,{index}),scale=flags=accurate_rnd+full_chroma_int+full_chroma_inp",
           "-pix_fmt", "rgb48be", "-frames:v", "1", "-f", "image2", "-c:v", "png", "-update", "1",
           str(out))  # fmt: skip
    return out.read_bytes()


def grab(lab: Lab, video_id: str, seconds: Rational) -> FrameGrabResult:
    result = lab.grabber().execute(
        GrabFrameCommand(video_asset_id=video_id, source_time=seconds), JobContext.detached()
    )
    assert isinstance(result, Ok), result
    return result.value


def picture_bytes(lab: Lab, asset_id: str) -> bytes:
    path = lab.library.local_path(asset_id)
    assert isinstance(path, Ok)
    return path.value.read_bytes()


def test_the_frame_on_screen_at_a_time_is_taken_exactly_as_16_bit_png(lab: Lab) -> None:
    video = moving_pattern(lab)
    video_id = import_video(lab, video, inspect=True)

    frame = grab(lab, video_id, Rational(1003, 1000))  # inside frame 25 (1.000-1.040 s)

    assert frame.position.exact and frame.position.frame_index == 25
    assert frame.inspection.how is InputHow.REUSED
    assert picture_bytes(lab, frame.asset.id) == reference_frame(lab, video, 25)
    assert picture_bytes(lab, frame.asset.id) != reference_frame(lab, video, 24)
    assert frame.asset.derivation is not None
    assert frame.asset.derivation.source_asset_id == video_id
    assert frame.asset.derivation.operation == FRAME_GRAB_OPERATION
    assert frame.asset.technical.get("bit_depth") == 16
    assert frame.asset.metadata["frame_index"] == 25

    again = grab(lab, video_id, Rational(1003, 1000))
    assert (again.created, again.asset.id) == (False, frame.asset.id)


def test_without_a_stored_inspection_the_frame_is_still_taken_but_not_called_exact(
    lab: Lab,
) -> None:
    video_id = import_video(lab, moving_pattern(lab), inspect=False)

    frame = grab(lab, video_id, Rational(1, 1))

    assert not frame.position.exact and frame.position.frame_index is None
    assert frame.inspection.how is InputHow.NOT_AVAILABLE


def test_a_time_past_the_end_is_refused(lab: Lab) -> None:
    video_id = import_video(lab, moving_pattern(lab), inspect=True)
    result = lab.resolve(FrameGrabber).execute(
        GrabFrameCommand(video_asset_id=video_id, source_time=Rational(10, 1)),
        JobContext.detached(),
    )
    assert isinstance(result, Err) and isinstance(result.error, FrameGrabFailed)


def test_a_video_request_is_grabbed_cut_out_and_turned_into_a_sticker(lab: Lab) -> None:
    path = lab.tmp / "product.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=0x5078A0:s=320x240:r=25:d=2", "-vf",
           "drawbox=x=100:y=60:w=120:h=100:color=0xC81E28@1:t=fill",
           "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(path))  # fmt: skip
    video_id = import_video(lab, path, inspect=True)
    preparer = lab.preparer()

    result = preparer.execute(
        PrepareVisualAssetsCommand(
            requests=(
                AssetRequest(
                    request_id="r",
                    source_asset_id=video_id,
                    source_time=Rational(1, 2),
                    variants=(VariantName.STICKER,),
                ),
                AssetRequest(
                    request_id="late", source_asset_id=video_id, source_time=Rational(9, 1)
                ),
            )
        ),
        JobContext.detached(),
    )

    assert isinstance(result, Ok), result
    document = result.value.document
    assert document.state is ResultState.PARTIAL  # the late request is past the end
    (frame,) = document.frames
    assert frame.exact and frame.frame_index == 12 and frame.video_asset_id == video_id
    clean = next(i for i in document.items if i.request_id == "r" and i.variant == "clean")
    assert clean.status is ItemStatus.OK and clean.derived_from_asset_id == frame.asset_id
    assert clean.source_asset_id == video_id
    sticker = next(i for i in document.items if i.variant == "sticker" and i.request_id == "r")
    assert sticker.status is ItemStatus.OK and sticker.kind is AssetKind.ENHANCED
    late = [i for i in document.items if i.request_id == "late"]
    assert late and all(i.status is ItemStatus.FAILED for i in late)
    assert "ffmpeg" in dict(document.provenance.engines)
