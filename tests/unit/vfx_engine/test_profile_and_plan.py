"""Configuration metadata vs checks, overrides, the plan envelope, its codec and render QC."""

from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime

import pytest

from media_house.core.domain import Rational
from media_house.modules.vfx_engine.domain.engine import (
    PlanMeta,
    RenderOutcome,
    apply_renders,
    build_plan,
    cache_key,
)
from media_house.modules.vfx_engine.domain.errors import InvalidVfxConfig, InvalidVfxDocument
from media_house.modules.vfx_engine.domain.model import OutputRef, RenderQc
from media_house.modules.vfx_engine.domain.profile import (
    PROFILES,
    VfxConfig,
    VfxProfile,
    knob_fields,
)
from media_house.modules.vfx_engine.domain.serialization import plan_from_json, plan_to_json
from media_house.modules.vfx_engine.domain.validation import judge_render, plan_problems
from media_house.modules.vfx_engine.domain.values import (
    EffectStatus,
    ResultState,
    TreatmentStatus,
)
from tests.support.vfx_fakes import clip, face_track, facts, inputs, request, shots

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _walk(group: object, prefix: str = ""):  # type: ignore[no-untyped-def]
    for f in fields(group):  # type: ignore[arg-type]
        value = getattr(group, f.name)
        if is_dataclass(value):
            yield from _walk(value, f"{prefix}{f.name}.")
        elif "label" in f.metadata:
            yield f"{prefix}{f.name}", f.metadata, value


def test_every_knob_has_metadata_and_the_checks_enforce_exactly_its_ranges() -> None:
    base = PROFILES["clean_enhancement"]
    knobs = list(_walk(base))
    assert len(knobs) > 60
    assert {path for path, _m, _d in knob_fields(VfxProfile)} == {p for p, _m, _v in knobs}
    for path, meta, value in knobs:
        if "choices" in meta:
            bad = VfxConfig(profile=base.name, overrides={path: "no-such-choice"})
            with pytest.raises(InvalidVfxConfig):
                bad.resolve()
            continue
        if "min" not in meta or isinstance(value, bool):
            continue
        step = meta.get("step", 1)
        for wrong in (meta["min"] - step, meta["max"] + step):
            wrong = round(wrong) if isinstance(value, int) else float(wrong)
            with pytest.raises(InvalidVfxConfig):
                VfxConfig(profile=base.name, overrides={path: wrong}).resolve()
        edge = meta["max"] if isinstance(value, int) else float(meta["max"])
        assert VfxConfig(profile=base.name, overrides={path: edge}).resolve().name == base.name


def test_every_built_in_profile_is_valid() -> None:
    assert set(PROFILES) == {
        "clean_enhancement", "talking_head", "product_detail", "demonstration",
        "fireworks_showcase", "cinematic", "vintage", "privacy_protection", "creative_effects",
        "shorts_energetic",
    }  # fmt: skip
    for profile in PROFILES.values():
        assert profile.problems() == [], profile.name


def test_overrides_are_typed_and_unknown_settings_are_refused() -> None:
    tuned = VfxConfig(overrides={"punch_in.scale": 1.2, "retime.freeze": 1}).resolve()
    assert tuned.punch_in.scale == 1.2 and tuned.retime.freeze == 1.0
    for bad in ({"punch_in.zoom": 1.2}, {"punch_in.scale": "big"}, {"name": "x"}, {"punch_in": 1}):
        with pytest.raises(InvalidVfxConfig):
            VfxConfig(overrides=bad).resolve()
    with pytest.raises(InvalidVfxConfig):
        VfxConfig(profile="no_such_profile").resolve()


def meta() -> PlanMeta:
    return PlanMeta(plan_version=1, supersedes=None, anchor_asset_id="a1", inputs=(), stages=())


def test_a_plan_is_valid_reproducible_and_round_trips_through_its_document() -> None:
    the_inputs = inputs(
        shots(clip(end=6), requests=[request(at=1.0)]),
        facts(tracks=[face_track()], flashes=[(3.0, 0.9)]),
    )
    profile = PROFILES["shorts_energetic"]
    plan = build_plan(the_inputs, profile, now=NOW, meta=meta())

    assert plan_problems(plan) == []
    assert plan_from_json(plan_to_json(plan)) == plan
    assert build_plan(the_inputs, profile, now=NOW, meta=meta()) == plan
    assert plan.limitations and plan.capabilities
    key = cache_key(
        the_inputs.shots, the_inputs.sources, None, the_inputs.available, VfxConfig(), profile,
        False, "",
    )  # fmt: skip
    changed = cache_key(
        replace(the_inputs.shots, output_width=1080, output_height=1920),
        the_inputs.sources, None, the_inputs.available, VfxConfig(), profile, False, "",
    )  # fmt: skip
    assert key != changed and key["config"] == changed["config"]


def test_a_document_of_another_schema_or_shape_is_refused() -> None:
    plan = build_plan(inputs(shots(), facts()), PROFILES["clean_enhancement"], now=NOW, meta=meta())
    text = plan_to_json(plan)
    with pytest.raises(InvalidVfxDocument):
        plan_from_json(text.replace('"schema_version": 1', '"schema_version": 99'))
    with pytest.raises(InvalidVfxDocument):
        plan_from_json(text.replace('"rendered": false', '"rendered": false, "extra": 1'))


def qc(**kw: object) -> RenderQc:
    base = RenderQc(
        width=640, height=360, pix_fmt="yuv422p10le", bit_depth=10,
        frame_rate=Rational(30, 1), frames_expected=120, frames_actual=120,
    )  # fmt: skip
    return replace(base, **kw)  # type: ignore[arg-type]


def test_render_qc_refuses_wrong_frames_8_bit_output_and_leaking_privacy() -> None:
    plan = build_plan(
        inputs(shots(), facts(tracks=[face_track()])),
        PROFILES["privacy_protection"],
        now=NOW,
        meta=meta(),
    )
    t = plan.treatments[0]
    limits = PROFILES["privacy_protection"].qc

    assert judge_render(t, qc(), limits) == ()
    codes = {
        i.code
        for i in judge_render(t, qc(frames_actual=119, pix_fmt="yuv420p", bit_depth=8), limits)
    }
    assert codes == {"frame_count", "pixel_format"}
    leak = judge_render(t, qc(concealment=0.2, coverage=0.9), limits)
    assert {i.code for i in leak} == {"privacy_leak", "privacy_coverage"}
    flicker = judge_render(t, qc(new_spikes=3, clipped_source=0.0, clipped_output=0.1), limits)
    assert {i.severity.value for i in flicker} == {"warning"}


def test_render_results_update_statuses_and_a_failure_makes_the_plan_partial() -> None:
    plan = build_plan(
        inputs(shots(clip("c1", end=3), clip("c2", start=3, end=6, at=3),
                     requests=[request(at=1.0)]), facts()),
        PROFILES["clean_enhancement"], now=NOW, meta=meta(),
    )  # fmt: skip
    t = plan.treatments[0]
    ok = RenderOutcome(
        treatment_id=t.treatment_id,
        render_fingerprint="fp",
        output=OutputRef(
            asset_id="r1", created=True, processing_fingerprint="fp", width=1, height=1, frames=90
        ),
        qc=qc(frames_expected=90, frames_actual=90),
        issues=(),
    )
    done = apply_renders(plan, {t.treatment_id: ok})
    assert done.treatments[0].status is TreatmentStatus.RENDERED
    assert done.treatments[0].active()[0].status is EffectStatus.RENDERED
    assert done.treatments[1].status is TreatmentStatus.ORIGINAL and done.state is ResultState.OK
    failed = apply_renders(plan, {t.treatment_id: replace(ok, output=None)})
    assert failed.treatments[0].status is TreatmentStatus.ERROR
    assert failed.state is ResultState.PARTIAL
    assert failed.treatments[0].effects[-1].fallback == "original footage"
