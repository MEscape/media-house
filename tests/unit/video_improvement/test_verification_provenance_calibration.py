"""Output checks, the provenance record and calibration from reference footage."""

import json
from dataclasses import replace

import pytest

from media_house.modules.video_improvement.application.calibration import derive_overrides
from media_house.modules.video_improvement.domain.color import OUTPUT_COLOR
from media_house.modules.video_improvement.domain.planning import (
    ColorPlan,
    DenoisePlan,
    PlannedOperation,
    ProcessingPlan,
    SharpenPlan,
)
from media_house.modules.video_improvement.domain.profiles import (
    apply_overrides,
    resolve_processing_profile,
)
from media_house.modules.video_improvement.domain.provenance import (
    OperationRecord,
    ProcessingProvenance,
)
from media_house.modules.video_improvement.domain.source import get_source_profile
from media_house.modules.video_improvement.domain.values import (
    METADATA_KEY,
    FrameRate,
    ProcessingStage,
    StageStatus,
)
from media_house.modules.video_improvement.domain.verification import (
    Check,
    CheckKind,
    hard_failures,
    regressed_stages,
    verify_output,
)
from tests.support.video_fakes import REC709, facts, measurements

PROFILE = resolve_processing_profile("natural")


def applied(stage: ProcessingStage, name: str) -> PlannedOperation:
    return PlannedOperation(stage, name, StageStatus.APPLIED, "because")


def plan_with(*stages: ProcessingStage) -> ProcessingPlan:
    operations = tuple(applied(s, s.value) for s in stages)
    return ProcessingPlan(
        operations,
        ColorPlan(REC709, exposure_stops=0.5) if ProcessingStage.COLOR in stages else None,
        DenoisePlan(0.5) if ProcessingStage.DENOISE in stages else None,
        SharpenPlan(0.4) if ProcessingStage.SHARPEN in stages else None,
    )


def verify(plan: ProcessingPlan, **changes: object) -> tuple[Check, ...]:
    arguments = {
        "source": facts(),
        "output": facts(),
        "source_frames": 250,
        "output_frames": 250,
        "before": measurements(),
        "predicted": measurements(),
        "after": measurements(),
        "plan": plan,
        "profile": PROFILE,
    } | changes
    return verify_output(**arguments)  # type: ignore[arg-type]


def named(checks: tuple[Check, ...], name: str) -> Check:
    return next(c for c in checks if c.name == name)


class TestHardChecks:
    def test_an_output_that_preserves_everything_passes(self) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR))

        assert not hard_failures(checks)
        assert regressed_stages(checks) == ()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("width", 1280),
            ("frame_rate", FrameRate(30, 1)),
            ("duration", 12.0),
            ("audio_stream_count", 0),
            ("rotation", 90),
        ],
    )
    def test_changing_what_must_be_preserved_is_a_hard_failure(
        self, field: str, value: object
    ) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR), output=facts(**{field: value}))

        failures = hard_failures(checks)
        assert failures
        assert all(f.kind is CheckKind.HARD for f in failures)

    def test_lost_or_added_frames_are_a_hard_failure(self) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR), output_frames=249)

        assert [c.name for c in hard_failures(checks)] == ["frame_count"]

    def test_unknown_frame_counts_are_not_compared(self) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR), source_frames=None)

        assert all(c.name != "frame_count" for c in checks)

    def test_a_duration_error_below_one_frame_is_tolerated(self) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR), output=facts(duration=10.03))

        assert not hard_failures(checks)

    def test_a_lost_timecode_is_reported_but_is_not_fatal(self) -> None:
        checks = verify(
            plan_with(ProcessingStage.COLOR),
            source=facts(timecode="01:00:00:00"),
            output=facts(timecode=None),
        )

        assert not named(checks, "timecode").passed
        assert not hard_failures(checks)


class TestQualityChecks:
    def test_more_clipping_than_the_guard_allows_blames_the_colour_stage(self) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR), after=measurements(highlight_clip=0.2))

        assert regressed_stages(checks) == (ProcessingStage.COLOR,)
        assert not named(checks, "clipping").passed

    def test_log_footage_is_judged_against_its_own_neutral_rendering(self) -> None:
        # the raw log signal never clips; the rendering to a display legitimately reaches white
        rendered = measurements(highlight_clip=0.0126)
        plan = plan_with(ProcessingStage.COLOR)

        naive = verify(plan, after=rendered)
        informed = verify(plan, after=rendered, color_reference=rendered)

        assert regressed_stages(naive) == (ProcessingStage.COLOR,)
        assert regressed_stages(informed) == ()

    def test_corrections_may_not_add_clipping_to_the_neutral_rendering(self) -> None:
        reference = measurements(highlight_clip=0.0126)
        worse = measurements(highlight_clip=0.2)

        checks = verify(plan_with(ProcessingStage.COLOR), after=worse, color_reference=reference)

        assert regressed_stages(checks) == (ProcessingStage.COLOR,)

    def test_a_grade_that_amplifies_noise_blames_the_colour_stage(self) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR), after=measurements(noise_sigma=0.03))

        assert not named(checks, "grade_noise").passed
        assert regressed_stages(checks) == (ProcessingStage.COLOR,)

    def test_noise_inside_the_tolerance_passes(self) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR), after=measurements(noise_sigma=0.0035))

        assert named(checks, "grade_noise").passed

    def test_crushed_blacks_blame_the_colour_stage(self) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR), after=measurements(shadow_crush=0.3))

        assert regressed_stages(checks) == (ProcessingStage.COLOR,)

    def test_output_that_does_not_match_the_prediction_is_flagged(self) -> None:
        checks = verify(plan_with(ProcessingStage.COLOR), after=measurements(luma_p50=0.6))

        assert not named(checks, "prediction").passed

    def test_the_output_must_be_tagged_rec709_when_the_colour_stage_ran(self) -> None:
        wrong = facts(color=replace(REC709, primaries=REC709.primaries.__class__.BT2020))

        checks = verify(plan_with(ProcessingStage.COLOR), output=wrong)

        assert not named(checks, "color_tags").passed

    def test_without_a_colour_stage_the_source_tags_must_survive(self) -> None:
        checks = verify(plan_with(ProcessingStage.DENOISE), output=facts(color=OUTPUT_COLOR))
        assert named(checks, "color_tags").passed

        lost = verify(
            plan_with(ProcessingStage.DENOISE), output=facts(color=facts().color.__class__())
        )
        assert not named(lost, "color_tags").passed

    def test_denoising_that_adds_noise_or_removes_detail_is_blamed_on_denoise(self) -> None:
        noisy = verify(plan_with(ProcessingStage.DENOISE), after=measurements(noise_sigma=0.02))
        soft = verify(plan_with(ProcessingStage.DENOISE), after=measurements(sharpness=0.02))

        assert regressed_stages(noisy) == (ProcessingStage.DENOISE,)
        assert regressed_stages(soft) == (ProcessingStage.DENOISE,)

    def test_sharpening_that_amplifies_noise_is_blamed_on_sharpen(self) -> None:
        checks = verify(plan_with(ProcessingStage.SHARPEN), after=measurements(noise_sigma=0.01))

        assert regressed_stages(checks) == (ProcessingStage.SHARPEN,)

    def test_stages_that_did_not_run_are_not_checked_or_blamed(self) -> None:
        checks = verify(plan_with(ProcessingStage.SHARPEN), after=measurements(highlight_clip=0.5))

        assert regressed_stages(checks) == ()
        assert all(c.name not in {"clipping", "crushed_blacks"} for c in checks)

    def test_several_regressed_stages_come_back_in_processing_order(self) -> None:
        checks = verify(
            plan_with(ProcessingStage.SHARPEN, ProcessingStage.COLOR, ProcessingStage.DENOISE),
            after=measurements(highlight_clip=0.5, noise_sigma=0.05),
        )

        assert regressed_stages(checks) == (
            ProcessingStage.DENOISE,
            ProcessingStage.COLOR,
            ProcessingStage.SHARPEN,
        )


def provenance() -> ProcessingProvenance:
    return ProcessingProvenance(
        source_asset_id="src-1",
        source_profile="gopro_hero_9",
        source_profile_version=1,
        source_profile_origin="inspection",
        source_profile_evidence="firmware HD9.01.01.60.00",
        processing_profile="outdoor",
        input_color_space="bt709/bt709",
        working_color_space="linear_rec709",
        output_color_space="bt709/bt709",
        operations=(
            OperationRecord(
                ProcessingStage.COLOR,
                "exposure",
                StageStatus.APPLIED,
                "underexposed",
                {"stops": 0.8},
                {"linear_p50": 0.05},
            ),
            OperationRecord(ProcessingStage.DENOISE, "denoise", StageStatus.SKIPPED, "clean"),
        ),
        engines={"encoder": "libx264"},
        processing_version=1,
        facts_source="inspection",
        before=measurements().summary(),
        after=measurements().summary(),
        warnings=("exposure varies",),
        look_sha256="ab" * 32,
    )


class TestProvenance:
    def test_it_survives_the_asset_metadata_round_trip(self) -> None:
        original = provenance()

        stored = json.loads(json.dumps(original.to_metadata()))

        assert ProcessingProvenance.from_metadata(stored) == original

    def test_it_states_what_the_spec_requires(self) -> None:
        record = provenance()

        assert record.inspection_used
        assert record.applied(ProcessingStage.COLOR)
        assert not record.applied(ProcessingStage.DENOISE)
        operation = record.operation("exposure")
        assert operation is not None
        assert operation.parameters["stops"] == 0.8
        assert operation.measured["linear_p50"] == 0.05
        assert (
            record.input_color_space,
            record.working_color_space,
            record.output_color_space,
        ) == (
            "bt709/bt709",
            "linear_rec709",
            "bt709/bt709",
        )

    def test_a_standalone_run_says_the_source_was_probed(self) -> None:
        assert not replace(provenance(), facts_source="probe").inspection_used

    @pytest.mark.parametrize(
        "damage",
        [
            lambda raw: raw.pop(METADATA_KEY),
            lambda raw: raw[METADATA_KEY].update(schema_version=99),
            lambda raw: raw[METADATA_KEY].update(operations="none"),
            lambda raw: raw[METADATA_KEY]["operations"][0].update(status="maybe"),
            lambda raw: raw[METADATA_KEY]["operations"][0].update(parameters={"x": [1]}),
            lambda raw: raw[METADATA_KEY]["before"].update(luma_p50="dark"),
            lambda raw: raw[METADATA_KEY].update(source_profile=None),
            lambda raw: raw.update({METADATA_KEY: "text"}),
        ],
    )
    def test_damaged_or_unknown_records_read_as_no_provenance(self, damage: object) -> None:
        raw = json.loads(json.dumps(provenance().to_metadata()))
        damage(raw)  # type: ignore[operator]

        assert ProcessingProvenance.from_metadata(raw) is None

    def test_an_asset_without_a_record_has_none(self) -> None:
        assert ProcessingProvenance.from_metadata({}) is None


class TestCalibration:
    references = [
        measurements(linear_p50=0.20, mean_saturation=0.30, noise_sigma=0.002, sharpness=0.15),
        measurements(linear_p50=0.16, mean_saturation=0.34, noise_sigma=0.003, sharpness=0.13),
    ]

    def test_it_derives_the_settings_under_which_the_references_are_left_alone(self) -> None:
        overrides = derive_overrides(self.references)

        assert overrides["color.target_median"] == pytest.approx(0.18)
        assert overrides["color.saturation_min"] < 0.32 < overrides["color.saturation_max"]  # type: ignore[operator]
        assert overrides["denoise.trigger_sigma"] == pytest.approx(0.0045)
        assert overrides["sharpen.soft_below"] == pytest.approx(0.112)

    def test_the_result_is_plain_deterministic_data_the_caller_can_store(self) -> None:
        one = derive_overrides(self.references)

        assert one == derive_overrides(list(reversed(self.references)))
        assert json.loads(json.dumps(one)) == one

    def test_the_overrides_are_valid_configuration(self) -> None:
        config = apply_overrides(
            get_source_profile("rec709"),
            resolve_processing_profile("natural"),
            derive_overrides(self.references),
        )

        assert config.processing.color.target_median == pytest.approx(0.18)

    def test_extreme_references_still_produce_valid_settings(self) -> None:
        extreme = [
            measurements(
                linear_p50=0.9,
                mean_saturation=0.99,
                noise_sigma=0.5,
                sharpness=0.0,
                luma_p1=0.0,
                luma_p99=0.1,
            )
        ]

        apply_overrides(
            get_source_profile("rec709"),
            resolve_processing_profile("natural"),
            derive_overrides(extreme),
        )

    def test_footage_like_the_references_is_then_left_alone(self) -> None:
        from media_house.modules.video_improvement.domain.planning import (
            plan_processing,
            refine_plan,
        )

        config = apply_overrides(
            get_source_profile("rec709"),
            resolve_processing_profile("natural"),
            derive_overrides(self.references),
        )
        for reference in self.references:
            first = plan_processing(reference, facts(), config.source, config.processing)
            result = refine_plan(first, reference, config.processing)
            assert result.color is None
            assert result.denoise is None
