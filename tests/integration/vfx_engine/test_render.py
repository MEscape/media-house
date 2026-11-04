"""The VFX engine against the real composition root, the real Media Library and real FFmpeg.

Real: the container, the library (synthetic H.264 footage made by FFmpeg: a detailed background,
a detailed 96 px "subject" moving right, a 3-frame white burst at 2 s), the catalogs, derived
storage, the cache, the renderer and its QC. Synthetic: what the clip "shows" (a stored video
analysis with the burst and a face track that follows the subject). Every render is verified
from the FILE (ffprobe + decoded pixels), not from the plan.
"""

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from media_house.bootstrap.application import Application
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.modules.vfx_engine.application.contracts import (
    RENDER_OPERATION,
    EffectStatus,
    EffectType,
    NothingToTreat,
    PlanVfxCommand,
    PlanVfxError,
    RequestKind,
    ResultState,
    TreatmentStatus,
    VfxCatalog,
    VfxClip,
    VfxConfig,
    VfxPlan,
    VfxPlanner,
    VfxRequest,
    VfxResult,
    VfxShots,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from tests.integration.vfx_engine.conftest import needs_ffmpeg
from tests.support.cont_library import store_analysis
from tests.support.vfx_fakes import make_subject_video, moving_box, q, subject_analysis

pytestmark = [pytest.mark.integration, needs_ffmpeg]


def prepare(app: Application, tmp_path: Path, *, store: bool = True) -> MediaAssetDto:
    folder = tmp_path / "footage"
    folder.mkdir(exist_ok=True)
    imported = app.container.resolve(MediaLibrary).import_file(
        make_subject_video(folder / "subject.mp4"), display_name="subject"
    )
    assert isinstance(imported, Ok), imported
    if store:
        store_analysis(app, imported.value.asset, subject_analysis(imported.value.asset))
    return imported.value.asset


def shots_for(asset: MediaAssetDto, *requests: VfxRequest) -> VfxShots:
    return VfxShots(
        edit=None,
        project_id=None,
        frame_rate=q(30.0),
        output_width=640,
        output_height=360,
        clips=(
            VfxClip(
                clip_id="c1",
                asset_id=asset.id,
                source_start=q(0.0),
                source_end=q(4.0),
                timeline_start=q(0.0),
            ),
        ),
        requests=requests,
    )


def run(
    app: Application,
    shots: VfxShots,
    profile: str,
    *,
    render: bool = True,
    overrides: dict[str, float | str] | None = None,
) -> Result[VfxResult, PlanVfxError]:
    config = VfxConfig(profile=profile, overrides=overrides or {})
    command = PlanVfxCommand(shots, config=config, render=render)
    return app.container.resolve(VfxPlanner).execute(command, JobContext.detached())


def rendered(app: Application, plan: VfxPlan) -> Path:
    t = plan.treatments[0]
    assert t.output is not None, t.checks
    path = app.container.resolve(MediaLibrary).local_path(t.output.asset_id)
    assert isinstance(path, Ok)
    return path.value


def probe(path: Path) -> dict[str, str]:
    out = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",  # noqa: S607
         "-show_entries", "stream=codec_name,profile,pix_fmt,bits_per_raw_sample,width,height,"
         "r_frame_rate,nb_read_frames", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    stream: dict[str, str] = {k: str(v) for k, v in json.loads(out)["streams"][0].items()}
    return stream


def frames(path: Path, *, width: int = 640, height: int = 360) -> NDArray[np.float64]:
    """Every frame as 16-bit luma (0-1)."""
    raw = subprocess.run(  # noqa: S603
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={width}:{height},format=gray16le",  # noqa: S607
         "-f", "rawvideo", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, dtype="<u2").reshape(-1, height, width).astype(np.float64) / 65535


def source_frames(app: Application, asset: MediaAssetDto) -> NDArray[np.float64]:
    path = app.container.resolve(MediaLibrary).local_path(asset.id)
    assert isinstance(path, Ok)
    return frames(path.value)


def sha(app: Application, asset: MediaAssetDto) -> str:
    path = app.container.resolve(MediaLibrary).local_path(asset.id)
    assert isinstance(path, Ok)
    return hashlib.sha256(path.value.read_bytes()).hexdigest()


def assert_prores_10_bit(path: Path, count: int) -> None:
    facts = probe(path)
    assert facts["codec_name"] == "prores"
    assert facts["pix_fmt"] == "yuv422p10le"
    assert facts["bits_per_raw_sample"] == "10"
    assert facts["nb_read_frames"] == str(count)
    assert (facts["width"], facts["height"], facts["r_frame_rate"]) == ("640", "360", "30/1")


def test_a_punch_in_renders_a_10_bit_derived_clip_and_is_reused(
    app: Application, tmp_path: Path
) -> None:
    asset = prepare(app, tmp_path)
    before = sha(app, asset)
    emphasis = VfxRequest(
        request_id="e1", kind=RequestKind.EMPHASIS, timeline_at=q(1.0), priority=0.9
    )
    first = run(app, shots_for(asset, emphasis), "clean_enhancement")
    assert isinstance(first, Ok), first
    plan = first.value.plan
    t = plan.treatments[0]

    assert plan.state is ResultState.OK and t.status is TreatmentStatus.RENDERED
    assert t.qc is not None and t.qc.low_precision_steps == () and t.qc.source_unchanged
    path = rendered(app, plan)
    assert_prores_10_bit(path, 120)
    out, src = frames(path), source_frames(app, asset)
    assert np.abs(out[10] - src[10]).mean() < 0.01  # before the punch: the original picture
    assert np.abs(out[40] - src[40]).mean() > 0.03  # punched in: a different (zoomed) picture
    output = app.container.resolve(MediaLibrary).get(t.output.asset_id)  # type: ignore[union-attr]
    assert isinstance(output, Ok)
    assert output.value.derivation is not None
    assert output.value.derivation.operation == RENDER_OPERATION
    assert output.value.derivation.source_asset_id == asset.id
    assert sha(app, asset) == before  # the original is never touched

    again = run(app, shots_for(asset, emphasis), "clean_enhancement")
    assert isinstance(again, Ok) and again.value.created is False
    harder = run(
        app, shots_for(asset, emphasis), "clean_enhancement", overrides={"punch_in.scale": 1.2}
    )
    assert isinstance(harder, Ok) and harder.value.created
    assert harder.value.plan.plan_version == 2
    assert harder.value.plan.supersedes == first.value.asset.id
    stored = app.container.resolve(VfxCatalog).latest(asset.id)
    assert isinstance(stored, Ok) and stored.value is not None
    assert stored.value[0] == harder.value.plan


def test_slow_motion_and_a_freeze_hold_the_burst_with_exact_frames(
    app: Application, tmp_path: Path
) -> None:
    asset = prepare(app, tmp_path)
    result = run(app, shots_for(asset), "fireworks_showcase")
    assert isinstance(result, Ok), result
    t = result.value.plan.treatments[0]
    kinds = {e.effect_type: e.status for e in t.effects}

    assert kinds[EffectType.SLOW_MOTION] is EffectStatus.RENDERED
    assert kinds[EffectType.FREEZE_FRAME] is EffectStatus.RENDERED
    assert t.time_map is not None
    path = rendered(app, result.value.plan)
    assert_prores_10_bit(path, 120)
    out = frames(path)
    hold = next(s for s in t.time_map.segments if s.kind.value == "freeze")
    held = out[hold.start_frame : hold.end_frame]
    assert np.abs(np.diff(held, axis=0)).max() < 1e-3  # an exact frame hold
    burst = (slice(45, 155), slice(225, 415))  # inside the white burst box
    assert held[0][burst].mean() > 0.85  # the held frame is the burst peak
    assert t.audio.value == "keep_original"


def test_vignette_and_grain_darken_the_edges_and_add_fine_noise_only(
    app: Application, tmp_path: Path
) -> None:
    asset = prepare(app, tmp_path)
    result = run(app, shots_for(asset), "vintage", overrides={"film_damage.apply": "never"})
    assert isinstance(result, Ok), result
    path = rendered(app, result.value.plan)
    assert_prores_10_bit(path, 120)
    out = frames(path)
    src = source_frames(app, asset)

    corner = (slice(0, 40), slice(0, 60))
    centre = (slice(160, 200), slice(300, 340))
    assert out[5][corner].mean() < src[5][corner].mean() * 0.85
    assert abs(out[5][centre].mean() - src[5][centre].mean()) < 0.03
    noise = (out[5] - src[5])[centre].std()
    assert 0.01 < noise < 0.08  # visible grain, nothing destroyed


def test_a_tracked_privacy_blur_covers_the_moving_face_and_nothing_else(
    app: Application, tmp_path: Path
) -> None:
    asset = prepare(app, tmp_path)
    result = run(app, shots_for(asset), "privacy_protection")
    assert isinstance(result, Ok), result
    t = result.value.plan.treatments[0]

    assert t.qc is not None and t.qc.coverage == 1.0
    assert t.qc.concealment is not None and t.qc.concealment > 0.8
    path = rendered(app, result.value.plan)
    assert_prores_10_bit(path, 120)
    out = frames(path)
    src = source_frames(app, asset)
    for n in (5, 47, 101):
        b = moving_box(n / 30)
        y0, y1, x0, x1 = round(b.y0 * 360), round(b.y1 * 360), round(b.x0 * 640), round(b.x1 * 640)
        face_src = np.abs(np.diff(src[n][y0:y1, x0:x1], axis=1)).mean()
        face_out = np.abs(np.diff(out[n][y0:y1, x0:x1], axis=1)).mean()
        assert face_out < face_src * 0.2, n  # the subject's detail is gone on every frame
        away = (slice(300, 360), slice(0, 80)) if b.x0 > 0.3 else (slice(300, 360), slice(560, 640))
        assert np.abs(out[n][away] - src[n][away]).mean() < 0.01  # the rest is untouched


def test_privacy_with_a_lost_track_falls_back_to_the_whole_picture_over_the_gap(
    app: Application, tmp_path: Path
) -> None:
    asset = prepare(app, tmp_path, store=False)
    analysis = subject_analysis(asset)
    lost = analysis.tracks[0]
    points = tuple(p for p in lost.points if not 1.0 < p.time.frame / 30 < 2.5)
    store_analysis(app, asset, replace(analysis, tracks=(replace(lost, points=points),)))
    result = run(app, shots_for(asset), "privacy_protection")
    assert isinstance(result, Ok), result
    privacy = next(
        e
        for e in result.value.plan.treatments[0].effects
        if e.effect_type is EffectType.PRIVACY_BLUR
    )

    assert privacy.masks[0].fallback_spans
    out = frames(rendered(app, result.value.plan))
    src = source_frames(app, asset)
    corner = (slice(300, 360), slice(0, 80))
    assert np.abs(out[60][corner] - src[60][corner]).mean() > 0.01  # whole picture concealed


def test_a_restrained_glow_lifts_only_the_surroundings_of_bright_areas(
    app: Application, tmp_path: Path
) -> None:
    asset = prepare(app, tmp_path)
    glow = VfxRequest(
        request_id="g1", kind=RequestKind.EXPLICIT, timeline_at=q(0.0), effect=EffectType.GLOW
    )
    result = run(
        app, shots_for(asset, glow), "creative_effects",
        overrides={"glow.threshold": 0.7, "glow.radius": 0.05, "glow.strength": 0.5},
    )  # fmt: skip
    assert isinstance(result, Ok), result
    path = rendered(app, result.value.plan)
    assert_prores_10_bit(path, 120)
    out = frames(path)
    src = source_frames(app, asset)
    # frame 61: the white burst box (x 220-420, y 40-160) blooms into the picture just above it
    halo = (slice(26, 38), slice(260, 380))
    assert out[61][halo].mean() > src[61][halo].mean() + 0.01
    assert (out[61] >= src[61] - 0.02).mean() > 0.97  # glow only adds light (screen blend)


def test_planning_only_without_a_picture_analysis_keeps_the_original(
    app: Application, tmp_path: Path
) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    imported = app.container.resolve(MediaLibrary).import_file(make_subject_video(folder / "p.mp4"))
    assert isinstance(imported, Ok)
    asset = imported.value.asset
    shots = VfxShots.no_speech(((asset.id, q(4.0)),), frame_rate=q(30.0), width=640, height=360)
    result = run(app, shots, "fireworks_showcase", render=False)
    assert isinstance(result, Ok), result
    plan = result.value.plan

    assert plan.treatments[0].status is TreatmentStatus.ORIGINAL
    assert plan.edit is None and not plan.rendered
    assert any(r.kind == "video_analysis" and r.how.value == "not_available" for r in plan.inputs)
    empty = app.container.resolve(VfxPlanner).execute(
        PlanVfxCommand(replace(shots, clips=())), JobContext.detached()
    )
    assert isinstance(empty, Err) and isinstance(empty.error, NothingToTreat)
