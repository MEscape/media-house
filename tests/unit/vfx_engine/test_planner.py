"""Effect selection: restraint first, every decision explained, exact spans."""

from dataclasses import replace
from fractions import Fraction

from media_house.modules.vfx_engine.domain.facts import Box, CropFact, ShotFact, SourceSpan, fr
from media_house.modules.vfx_engine.domain.planner import plan_treatments
from media_house.modules.vfx_engine.domain.profile import (
    PROFILES,
    EffectDecision,
    SectionOverride,
    VfxConfig,
)
from media_house.modules.vfx_engine.domain.values import (
    AudioIntent,
    ClipAudio,
    Decision,
    EffectStatus,
    EffectType,
    MaskKind,
    RequestKind,
    Severity,
    TreatmentStatus,
)
from tests.support.vfx_fakes import (
    ALL_EFFECTS,
    clip,
    face_track,
    facts,
    inputs,
    q,
    request,
    shots,
    style,
)


def plan(profile: str, the_inputs):  # type: ignore[no-untyped-def]
    return plan_treatments(the_inputs, PROFILES[profile])


def reasons(treatment, kind: EffectType) -> str:  # type: ignore[no-untyped-def]
    return " ".join(next(e for e in treatment.effects if e.effect_type is kind).reasons)


def statuses(treatment) -> dict[EffectType, EffectStatus]:  # type: ignore[no-untyped-def]
    return {e.effect_type: e.status for e in treatment.effects}


def test_good_footage_without_requests_gets_an_explicit_no_effect() -> None:
    for name in ("clean_enhancement", "talking_head", "fireworks_showcase", "product_detail"):
        result = plan(name, inputs(shots(), facts()))
        t = result.treatments[0]

        assert t.status is TreatmentStatus.ORIGINAL, name
        assert [e.effect_type for e in t.effects] == [EffectType.NO_EFFECT], name
        assert t.effects[0].reasons


def test_the_no_speech_path_plans_without_edit_sections_or_speech() -> None:
    no_speech = shots(clip(end=6), edit=None, project_id=None)
    result = plan("fireworks_showcase", inputs(no_speech, facts(flashes=[(3.0, 0.9)])))
    t = result.treatments[0]

    assert t.section_id is None
    assert statuses(t)[EffectType.SLOW_MOTION] is EffectStatus.PLANNED
    assert statuses(t)[EffectType.FREEZE_FRAME] is EffectStatus.PLANNED


def test_fireworks_slow_motion_and_freeze_sit_on_the_strongest_burst() -> None:
    result = plan(
        "fireworks_showcase",
        inputs(shots(clip(end=8)), facts(flashes=[(2.0, 0.6), (5.0, 0.95), (6.0, 0.3)])),
    )
    t = result.treatments[0]
    assert t.time_map is not None
    freeze = next(s for s in t.time_map.segments if s.kind.value == "freeze")

    assert fr(freeze.source_start) == 5
    assert t.audio is AudioIntent.KEEP_ORIGINAL
    assert statuses(t).get(EffectType.GLOW) is None  # no bloom on fireworks by default
    assert fr(t.timeline_end) - fr(t.timeline_start) == 8  # the clip keeps its span


def test_a_burst_below_the_trigger_strength_does_nothing() -> None:
    result = plan("fireworks_showcase", inputs(shots(clip(end=8)), facts(flashes=[(2.0, 0.2)])))

    assert result.treatments[0].status is TreatmentStatus.ORIGINAL


def test_retiming_over_synced_speech_is_refused() -> None:
    talking = clip(end=8, speech=(SourceSpan(source_start=q(4.5), source_end=q(6.0)),))
    result = plan("fireworks_showcase", inputs(shots(talking), facts(flashes=[(5.0, 0.9)])))
    t = result.treatments[0]

    assert statuses(t)[EffectType.SLOW_MOTION] is EffectStatus.REJECTED
    assert "lip sync" in reasons(t, EffectType.SLOW_MOTION)
    voiceover = replace(talking, audio=ClipAudio.VOICEOVER)
    again = plan("fireworks_showcase", inputs(shots(voiceover), facts(flashes=[(5.0, 0.9)])))
    assert statuses(again.treatments[0])[EffectType.SLOW_MOTION] is EffectStatus.PLANNED


def test_an_emphasis_request_becomes_a_punch_in_with_exact_frames() -> None:
    result = plan(
        "clean_enhancement",
        inputs(shots(clip(end=6), requests=[request(at=1.25)]), facts()),
    )
    punch = next(e for e in result.treatments[0].effects if e.effect_type is EffectType.PUNCH_IN)

    assert punch.status is EffectStatus.PLANNED
    assert punch.start_frame == round(Fraction(5, 4) * 30)
    assert punch.param("scale") == 1.1
    assert punch.request_id is not None


def test_the_punch_in_never_drops_below_the_minimum_effective_resolution() -> None:
    small = facts(width=1280, height=720)  # 720 px source into a 1080 px edit
    result = plan("clean_enhancement", inputs(shots(requests=[request()]), small))
    punch = next(e for e in result.treatments[0].effects if e.effect_type is EffectType.PUNCH_IN)

    assert punch.status is EffectStatus.BYPASSED
    assert "already enlarged" in " ".join(punch.reasons)
    big = facts(width=3840, height=2160)
    limited = plan("product_detail", inputs(shots(requests=[request()]), big))
    p2 = next(e for e in limited.treatments[0].effects if e.effect_type is EffectType.PUNCH_IN)
    assert p2.status is EffectStatus.PLANNED and p2.param("scale") == 1.2


def test_a_punch_in_follows_reframings_window_so_a_later_crop_keeps_the_subject() -> None:
    window = Box(x0=0.6, y0=0.0, x1=0.9, y1=1.0)
    source = replace(
        facts(width=3840, height=2160),
        reframing_id="refr-1",
        crops=(
            CropFact(
                recommendation_id="r1",
                source_start=q(0.0),
                source_end=q(4.0),
                keyframes=((q(0.0), window),),
            ),
        ),
    )
    result = plan("clean_enhancement", inputs(shots(requests=[request()]), source))
    punch = next(e for e in result.treatments[0].effects if e.effect_type is EffectType.PUNCH_IN)

    assert abs(punch.param("anchor_x") - 0.75) < 1e-6
    assert "reframing" in " ".join(punch.reasons)


def test_the_channel_style_caps_the_intensity() -> None:
    result = plan(
        "fireworks_showcase",
        inputs(shots(clip(end=8)), facts(flashes=[(3.0, 0.9)]), style=style(ceiling=1)),
    )
    t = result.treatments[0]

    assert statuses(t)[EffectType.SLOW_MOTION] is EffectStatus.REJECTED
    assert "ceiling 1" in reasons(t, EffectType.SLOW_MOTION)


def test_an_explicit_request_above_the_ceiling_is_honoured_with_a_warning() -> None:
    req = request(RequestKind.EXPLICIT, at=1.0, effect=EffectType.CHROMATIC_ABERRATION)
    result = plan("clean_enhancement", inputs(shots(requests=[req]), facts()))

    assert statuses(result.treatments[0])[EffectType.CHROMATIC_ABERRATION] is EffectStatus.PLANNED
    assert any("ceiling" in w for w in result.warnings)


def test_emphasis_is_not_repeated_on_neighbouring_clips() -> None:
    clips = (clip("c1", end=3), clip("c2", start=3, end=6, at=3))
    reqs = [request(at=1.0), request(at=4.0)]
    result = plan("clean_enhancement", inputs(shots(*clips, requests=reqs), facts()))
    second = statuses(result.treatments[1])

    assert statuses(result.treatments[0])[EffectType.PUNCH_IN] is EffectStatus.PLANNED
    assert second[EffectType.PUNCH_IN] is EffectStatus.REJECTED


def test_a_persons_rejection_wins_and_an_acceptance_lifts_the_spacing_rule() -> None:
    clips = (clip("c1", end=3), clip("c2", start=3, end=6, at=3))
    reqs = [request(at=1.0), request(at=4.0)]
    config = VfxConfig(
        decisions=(
            EffectDecision(effect_key="c1:punch_in", decision=Decision.REJECTED),
            EffectDecision(effect_key="c2:punch_in", decision=Decision.ACCEPTED),
        )
    )
    result = plan("clean_enhancement", inputs(shots(*clips, requests=reqs), facts(), config=config))

    assert statuses(result.treatments[0])[EffectType.PUNCH_IN] is EffectStatus.REJECTED
    assert statuses(result.treatments[1])[EffectType.PUNCH_IN] is EffectStatus.PLANNED


def test_looks_protect_footage_that_is_already_dark_noisy_or_clipping() -> None:
    night = ShotFact(
        shot_id="s0",
        source_start=q(0.0),
        source_end=q(60.0),
        luma_p50=0.04,
        clipped=0.03,
        noise_sigma=0.05,
    )
    result = plan("cinematic", inputs(shots(), facts(shots_=[night])))
    s = statuses(result.treatments[0])

    assert s[EffectType.GLOW] is EffectStatus.BYPASSED
    assert s[EffectType.VIGNETTE] is EffectStatus.BYPASSED
    assert s[EffectType.FILM_GRAIN] is EffectStatus.BYPASSED
    assert result.treatments[0].status is TreatmentStatus.ORIGINAL
    day = replace(night, luma_p50=0.4, clipped=0.0, noise_sigma=0.005)
    lit = statuses(plan("cinematic", inputs(shots(), facts(shots_=[day]))).treatments[0])
    assert lit[EffectType.VIGNETTE] is EffectStatus.PLANNED


def test_section_overrides_change_only_their_section() -> None:
    clips = (clip("c1", end=3, section_id="a"), clip("c2", start=3, end=6, at=3, section_id="b"))
    config = VfxConfig(
        profile="clean_enhancement",
        section_overrides=(SectionOverride(section_id="b", overrides={"vignette.apply": "auto"}),),
    )
    day = ShotFact(shot_id="s0", source_start=q(0.0), source_end=q(60.0), luma_p50=0.4)
    result = plan_treatments(
        inputs(shots(*clips), facts(shots_=[day]), config=config), config.resolve()
    )

    assert result.treatments[0].status is TreatmentStatus.ORIGINAL
    assert statuses(result.treatments[1])[EffectType.VIGNETTE] is EffectStatus.PLANNED


def test_privacy_masks_contain_every_tracked_box_and_follow_the_track() -> None:
    result = plan("privacy_protection", inputs(shots(), facts(tracks=[face_track()])))
    t = result.treatments[0]
    privacy = next(e for e in t.effects if e.effect_type is EffectType.PRIVACY_BLUR)

    assert privacy.status is EffectStatus.PLANNED
    assert privacy.masks[0].kind is MaskKind.TRACKED
    assert "not removal" in " ".join(privacy.reasons)
    assert not [c for c in result.checks if c.code == "privacy_coverage"]


def test_privacy_bridges_short_gaps_and_conceals_the_whole_picture_over_long_ones() -> None:
    lost = face_track(confidence=lambda t: 0.0 if 1.0 < t < 2.5 else 0.9)
    result = plan("privacy_protection", inputs(shots(), facts(tracks=[lost])))
    privacy = next(
        e for e in result.treatments[0].effects if e.effect_type is EffectType.PRIVACY_BLUR
    )

    spans = privacy.masks[0].fallback_spans
    assert spans and fr(spans[0][0]) <= Fraction(11, 10) and fr(spans[0][1]) >= Fraction(12, 5)
    assert "whole picture" in " ".join(privacy.reasons)


def test_privacy_without_a_picture_analysis_is_flagged_never_assumed_safe() -> None:
    result = plan("privacy_protection", inputs(shots(), facts(analysed=False)))

    assert any(c.code == "privacy_unverified" for c in result.checks)
    strict = VfxConfig(profile="privacy_protection", overrides={"privacy.unverified": "full_frame"})
    again = plan_treatments(inputs(shots(), facts(analysed=False), config=strict), strict.resolve())
    privacy = next(
        e for e in again.treatments[0].effects if e.effect_type is EffectType.PRIVACY_BLUR
    )
    assert privacy.masks[0].kind is MaskKind.FULL_FRAME
    assert any(c.severity is Severity.ERROR for c in again.checks)


def test_the_main_subject_is_not_concealed_unless_asked() -> None:
    main = ShotFact(
        shot_id="s0", source_start=q(0.0), source_end=q(60.0), main_subject_track_id="f1"
    )
    result = plan(
        "privacy_protection", inputs(shots(), facts(shots_=[main], tracks=[face_track("f1")]))
    )
    assert result.treatments[0].status is TreatmentStatus.ORIGINAL
    asked = request(RequestKind.PRIVACY, at=0.5, track_ids=("f1",))
    again = plan(
        "privacy_protection",
        inputs(shots(requests=[asked]), facts(shots_=[main], tracks=[face_track("f1")])),
    )
    assert statuses(again.treatments[0])[EffectType.PRIVACY_BLUR] is EffectStatus.PLANNED


def test_effects_without_a_backend_are_not_available_and_name_a_safer_option() -> None:
    reqs = [
        request(RequestKind.EXPLICIT, at=0.5, effect=EffectType.OBJECT_REMOVAL),
        request(RequestKind.EXPLICIT, at=1.5, effect=EffectType.SCREEN_REPLACEMENT),
    ]
    result = plan("demonstration", inputs(shots(requests=reqs), facts()))
    found = {e.effect_type: e for e in result.treatments[0].effects}

    removal = found[EffectType.OBJECT_REMOVAL]
    assert removal.status is EffectStatus.NOT_AVAILABLE
    assert "conceal" in " ".join(removal.reasons)
    assert found[EffectType.SCREEN_REPLACEMENT].status is EffectStatus.NOT_AVAILABLE
    talking = plan(
        "talking_head",
        inputs(
            shots(),
            facts(shots_=[ShotFact(shot_id="s0", source_start=q(0.0), source_end=q(9.0),
                                   main_subject_track_id="p1")]),
            available=ALL_EFFECTS,
        ),
    )  # fmt: skip
    blur = statuses(talking.treatments[0])[EffectType.BACKGROUND_BLUR]
    assert blur is EffectStatus.NOT_AVAILABLE


def test_a_composite_needs_footage_on_a_higher_layer() -> None:
    clips = (clip("main", end=6), clip("cam", asset="a2", start=0, end=3, at=1, layer=1))
    req = request(RequestKind.EXPLICIT, at=2.0, effect=EffectType.COMPOSITE)
    result = plan("demonstration", inputs(shots(*clips, requests=[req]), facts(), facts("a2")))
    composite = next(
        e for e in result.treatments[0].effects if e.effect_type is EffectType.COMPOSITE
    )

    assert composite.status is EffectStatus.PLANNED
    assert (composite.start_frame, composite.end_frame) == (30, 120)
    lonely = plan("demonstration", inputs(shots(clip(), requests=[req]), facts()))
    assert statuses(lonely.treatments[0])[EffectType.COMPOSITE] is EffectStatus.REJECTED


def test_profiles_are_selected_by_format_and_the_default_is_restraint() -> None:
    assert VfxConfig().resolve().name == "clean_enhancement"
    assert VfxConfig().resolve("short").name == "shorts_energetic"
    assert VfxConfig().resolve("talking_head").name == "talking_head"
    assert VfxConfig(profile="vintage").resolve("short").name == "vintage"


def test_no_bloom_on_a_night_sky_whose_bursts_clip_locally() -> None:
    sky = ShotFact(
        shot_id="s0", source_start=q(0.0), source_end=q(60.0), luma_p50=0.008, clipped=0.0002
    )
    result = plan("cinematic", inputs(shots(), facts(shots_=[sky])))

    assert statuses(result.treatments[0])[EffectType.GLOW] is EffectStatus.BYPASSED
    assert "night picture" in reasons(result.treatments[0], EffectType.GLOW)
    assert statuses(result.treatments[0])[EffectType.FILM_GRAIN] is EffectStatus.BYPASSED
    assert result.treatments[0].status is TreatmentStatus.ORIGINAL
