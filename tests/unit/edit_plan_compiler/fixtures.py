"""Representative inputs for the compiler: a two-clip talking-head edit with cover footage, a
linked voice, one graphic, captions, transitions, a VFX clip and a soundtrack.

Timeline at 25 fps: A 0-4 s, B 4-8 s (main), cover C 5-6 s on layer 1, voice 0-8 s linked to A/B.
"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.engine import CompileMeta
from media_house.modules.edit_plan_compiler.domain.facts import (
    AssetFacts,
    BrandAssetFact,
    CaptionFact,
    CaptionsFacts,
    CompileInputs,
    CropFact,
    DerivativeFact,
    DuckFact,
    EditRef,
    EffectFact,
    FontFact,
    FramingRef,
    GraphicFact,
    GraphicsFacts,
    MusicSegmentFact,
    OriginalAudioFact,
    PlanStamp,
    ReframeFacts,
    SfxFact,
    SoundFacts,
    SourceBridge,
    SourceClip,
    SourceSection,
    StyleFacts,
    TimelineSource,
    TimeSegmentFact,
    TransitionFact,
    TransitionsFacts,
    TreatmentFact,
    VfxFacts,
    ZoneFact,
)

FPS = Rational(25, 1)
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
EDIT = EditRef(edit_plan_id="edit-1", plan_version=1)
STYLE_FP = "f" * 64


def r(n: int, d: int = 1) -> Rational:
    return Rational(n, d)


def stamp(plan_id: str, *, style: str | None = STYLE_FP, edit: EditRef | None = EDIT) -> PlanStamp:
    return PlanStamp(
        plan_id=plan_id,
        plan_version=1,
        document_id=f"doc-{plan_id}",
        style_fingerprint=style,
        state="ok",
        edit=edit,
    )


def clip(
    clip_id: str,
    asset: str,
    s0: int,
    s1: int,
    t0: int,
    *,
    picture: bool = True,
    layer: int = 0,
    linked: str | None = None,
    section: str | None = "s1",
    framing: tuple[FramingRef, ...] = (),
) -> SourceClip:
    return SourceClip(
        clip_id=clip_id,
        asset_id=asset,
        source_start=r(s0),
        source_end=r(s1),
        timeline_start=r(t0),
        timeline_duration=r(s1 - s0),
        picture=picture,
        layer=layer,
        section_id=section,
        start_frame=t0 * 25,
        duration_frames=(s1 - s0) * 25,
        linked=linked,
        framing=framing,
    )


def asset(
    asset_id: str,
    *,
    duration: int = 60,
    audio: bool = True,
    video: bool = True,
    fps: Rational | None = FPS,
    variable: bool = False,
    original: str | None = None,
    operation: str | None = None,
    media: str = "video",
    derivatives: tuple[DerivativeFact, ...] = (),
    available: bool = True,
) -> AssetFacts:
    return AssetFacts(
        asset_id=asset_id,
        exists=True,
        file_available=available,
        media_type=media,
        original_asset_id=original,
        operation=operation,
        checksum=f"sha-{asset_id}",
        duration=r(duration),
        frame_rate=fps if video else None,
        frame_rate_variable=variable,
        has_video=video,
        has_audio=audio,
        sample_rate=48_000 if audio else None,
        channels=2 if audio else None,
        width=1920 if video else None,
        height=1080 if video else None,
        derivatives=derivatives,
        inspected=True,
    )


def source() -> TimelineSource:
    return TimelineSource(
        edit=EDIT,
        project_id="proj-1",
        anchor_asset_id="rec1",
        frame_rate=FPS,
        frame_rate_assumed=False,
        duration=r(8),
        story_format="talking_head",
        clips=(
            clip("pA", "rec1", 10, 14, 0),
            clip("pB", "rec1", 20, 24, 4),
            clip("pC", "broll1", 0, 1, 5, layer=1),
            clip("aV", "rec1", 10, 14, 0, picture=False, linked="pA"),
            clip("aW", "rec1", 20, 24, 4, picture=False, linked="pB"),
        ),
        sections=(
            SourceSection(
                section_id="s1", order=0, label="Hook", timeline_start=r(0), timeline_end=r(8)
            ),
        ),
        bridges=(),
    )


def style(**kw: object) -> StyleFacts:
    base = StyleFacts(
        document_id="style-doc",
        fingerprint=STYLE_FP,
        identity_id="mesc",
        identity_version=2,
        profile="talking_head",
        width=1920,
        height=1080,
        sponsored=False,
        zones=(
            ZoneFact(name="caption_band", kind="caption", left=0, top=800, width=1920, height=200),
            ZoneFact(name="lower_third", kind="graphic", left=60, top=700, width=900, height=160),
        ),
        fonts=(FontFact(key="display", family="Anton", weight=400, sha256="aa", available=True),),
        brand_assets=(
            BrandAssetFact(slot="logo", purpose="corner logo", required=True, asset_id="logo1"),
        ),
        rules=("no_pure_white", "caption_zone_only"),
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def graphics() -> GraphicsFacts:
    return GraphicsFacts(
        stamp=stamp("gp"),
        graphics=(
            GraphicFact(
                graphic_id="g1",
                kind="lower_third",
                status="ok",
                timeline_start=r(1),
                duration=r(2),
                start_frame=25,
                end_frame=75,
                asset_id="gfx1",
                asset_start_frame=0,
                has_alpha=True,
                width=1920,
                height=1080,
                zone="lower_third",
                section_id="s1",
                origin="editorial",
                render_fingerprint="rf1",
                fingerprint="fp1",
            ),
        ),
        timeline_end=None,
    )


def captions() -> CaptionsFacts:
    return CaptionsFacts(
        stamp=stamp("cp"),
        mode="selective_captions",
        segments=(
            CaptionFact(
                segment_id="cap:1",
                clip_ids=("pA",),
                section_id="s1",
                text="hello there",
                timeline_start=r(0),
                timeline_end=r(2),
                start_frame=0,
                end_frame=50,
                render_status="rendered",
                asset_id="cap-file",
                asset_start_frame=0,
                zone="caption_band",
                look="word_group",
                word_count=2,
                emphasised=1,
                fingerprint="c1",
            ),
            CaptionFact(
                segment_id="cap:2",
                clip_ids=("pB",),
                section_id="s1",
                text="second",
                timeline_start=r(4),
                timeline_end=r(6),
                start_frame=100,
                end_frame=150,
                render_status="rendered",
                asset_id="cap-file",
                asset_start_frame=50,
                zone="caption_band",
                look="word_group",
                word_count=1,
                emphasised=0,
                fingerprint="c2",
            ),
        ),
        language="en",
        srt="1\n00:00:00,000 --> 00:00:02,000\nhello there\n",
        vtt="WEBVTT\n",
    )


def transition(
    kind: str = "cross_dissolve",
    *,
    family: str = "blend",
    handles: tuple[int, int] = (5, 5),
    available: tuple[int | None, int | None] = (200, 200),
    overlay: str | None = None,
    status: str = "recommended",
) -> TransitionFact:
    return TransitionFact(
        transition_id="cut:pA>pB",
        boundary="cut",
        outgoing_clip_id="pA",
        incoming_clip_id="pB",
        kind=kind,
        family=family,
        preset_id="soft",
        alignment="center",
        direction="",
        intensity=1,
        status=status,
        cut_at=r(4),
        cut_frame=100,
        timeline_start=r(4) if overlay else r(39, 10),
        timeline_end=r(41, 10),
        start_frame=98 if not overlay else 100,
        end_frame=102,
        duration_frames=4,
        required_out=handles[0],
        required_in=handles[1],
        available_out=available[0],
        available_in=available[1],
        fallback_wanted=None,
        fallback_used=None,
        fallback_reason="",
        overlay_asset_id=overlay,
        overlay_state="rendered" if overlay else "not_requested",
        overlay_start=r(39, 10) if overlay else None,
        overlay_duration=r(2, 10) if overlay else None,
        audio_kind="crossfade",
        audio_curve="equal_power",
        audio_at=r(4),
        audio_before=r(1, 20),
        audio_after=r(1, 20),
        audio_sample_rate=48_000,
    )


def transitions(*items: TransitionFact) -> TransitionsFacts:
    return TransitionsFacts(stamp=stamp("tp"), transitions=items or (transition(),))


def vfx(status: str = "rendered") -> VfxFacts:
    segs = (
        TimeSegmentFact(
            kind="normal",
            timeline_start=r(0),
            timeline_end=r(2),
            source_start=r(10),
            source_end=r(12),
            rate=r(1),
            start_frame=0,
            end_frame=50,
            audio="source_sync",
        ),
        TimeSegmentFact(
            kind="slow",
            timeline_start=r(2),
            timeline_end=r(4),
            source_start=r(12),
            source_end=r(13),
            rate=r(1, 2),
            start_frame=50,
            end_frame=100,
            audio="source_sync",
        ),
    )
    return VfxFacts(
        stamp=stamp("vp"),
        treatments=(
            TreatmentFact(
                treatment_id="t1",
                clip_id="pA",
                status=status,
                effects=(
                    EffectFact(
                        effect_id="e1",
                        effect_type="slow_motion",
                        status=status if status != "original" else "planned",
                        preset="half",
                        mode="",
                        parameters=(("rate", 0.5),),
                        intensity=1,
                        timeline_start=r(2),
                        timeline_end=r(4),
                        start_frame=50,
                        end_frame=100,
                        backend="ffmpeg",
                        fallback="original footage",
                    ),
                ),
                segments=segs,
                interpolation="blend",
                audio="source_sync",
                output_asset_id="vfx-out" if status in {"rendered", "reused"} else None,
                output_frames=100,
                render_fingerprint="rf",
            ),
        ),
    )


def sound() -> SoundFacts:
    return SoundFacts(
        stamp=PlanStamp(
            plan_id="sp",
            plan_version=1,
            document_id="doc-sp",
            style_fingerprint=None,
            state="ok",
            edit=EDIT,
        ),
        identity="mesc_sound",
        identity_version=1,
        music=(
            MusicSegmentFact(
                cue_id="mus:1",
                segment_id="seg1",
                asset_id="song1",
                title="Song",
                role="body",
                timeline_start=r(0),
                timeline_end=r(8),
                source_start=r(5),
                source_end=r(13),
                gain_db=-18.0,
                fade_in=r(1),
                fade_out=r(2),
                licence_state="cleared",
            ),
        ),
        sfx=(
            SfxFact(
                item_id="sfx:1",
                asset_id="whoosh1",
                title="Whoosh",
                purpose="transition",
                timeline_start=r(39, 10),
                timeline_end=r(43, 10),
                source_start=r(0),
                source_end=r(1, 2),
                gain_db=-6.0,
                licence_state="cleared",
            ),
        ),
        ducking=(
            DuckFact(
                duck_id="d1",
                cue_id="mus:1",
                key="speech",
                timeline_start=r(0),
                timeline_end=r(8),
                depth_db=-10.0,
                attack_seconds=0.1,
                release_seconds=0.3,
            ),
        ),
        original_audio=(
            OriginalAudioFact(
                clip_id="aV",
                asset_id="rec1",
                timeline_start=r(0),
                timeline_end=r(4),
                mode="speech",
                music_over_it=True,
            ),
        ),
        credits="Song by X",
        plan_hash="h",
    )


def assets() -> Mapping[str, AssetFacts]:
    return {
        "rec1": asset("rec1", duration=60),
        "broll1": asset("broll1", duration=30, audio=False),
        "gfx1": asset(
            "gfx1", duration=4, audio=False, original="rec1", operation="motion_graphic_render"
        ),
        "cap-file": asset(
            "cap-file", duration=6, audio=False, original="rec1", operation="motion_graphic_render"
        ),
        "vfx-out": asset(
            "vfx-out", duration=4, audio=False, original="rec1", operation="vfx_render"
        ),
        "song1": asset("song1", duration=120, video=False, media="audio"),
        "whoosh1": asset("whoosh1", duration=2, video=False, media="audio"),
    }


def inputs(**kw: object) -> CompileInputs:
    base = CompileInputs(
        source=source(),
        style=style(),
        graphics=graphics(),
        captions=captions(),
        transitions=transitions(),
        vfx=vfx(),
        sound=sound(),
        assets=assets(),
        reframing={},
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def meta(version: int = 1, supersedes: str | None = None) -> CompileMeta:
    return CompileMeta(
        compilation_version=version, supersedes=supersedes, compiled_at=NOW, inputs=()
    )


__all__ = [
    "CropFact",
    "FramingRef",
    "ReframeFacts",
    "SourceBridge",
    "TimelineSource",
    "asset",
    "assets",
    "captions",
    "clip",
    "graphics",
    "inputs",
    "meta",
    "r",
    "sound",
    "source",
    "stamp",
    "style",
    "transition",
    "transitions",
    "vfx",
]
