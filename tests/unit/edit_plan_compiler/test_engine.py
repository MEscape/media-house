from dataclasses import replace

from media_house.modules.edit_plan_compiler.domain.engine import compile_timeline
from media_house.modules.edit_plan_compiler.domain.facts import EditRef
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.edit_plan_compiler.domain.values import (
    Execution,
    IssueClass,
    ResultState,
    TrackRole,
)
from tests.unit.edit_plan_compiler import fixtures as f


def compiled(**kw: object):  # type: ignore[no-untyped-def]
    return compile_timeline(f.inputs(**kw), CompilerConfig(), meta=f.meta())


def codes(result, klass: IssueClass | None = None) -> set[str]:  # type: ignore[no-untyped-def]
    pool = list(result.spec.issues) + list(result.critical)
    return {i.code for i in pool if klass is None or i.klass is klass}


class TestBasicCompilation:
    def test_full_edit_compiles_without_critical_findings(self) -> None:
        result = compiled()

        assert result.critical == (), result.critical
        spec = result.spec
        assert spec.timebase.duration_frames == 200
        assert [v.item_id for v in spec.video] == ["v:pA", "v:pB", "v:pC"]

    def test_main_footage_is_on_v1_and_cover_footage_on_a_higher_track(self) -> None:
        spec = compiled().spec

        tracks = {t.track_id: t for t in spec.tracks}
        by_clip = {v.clip_id: tracks[v.track_id] for v in spec.video}
        assert by_clip["pA"].track_id == "V1" and by_clip["pA"].role is TrackRole.PRIMARY
        assert by_clip["pC"].role is TrackRole.COVER
        assert tracks["V2"].role is TrackRole.COVER

    def test_frames_are_computed_once_from_exact_times(self) -> None:
        spec = compiled().spec

        b = next(v for v in spec.video if v.clip_id == "pB")
        assert (b.start_frame, b.end_frame, b.frames) == (100, 200, 100)

    def test_every_asset_traces_to_its_recording(self) -> None:
        spec = compiled().spec

        by_id = {a.asset_id: a for a in spec.assets}
        assert by_id["vfx-out"].original_asset_id == "rec1"
        assert by_id["gfx1"].relation.value == "rendered"
        assert by_id["song1"].relation.value == "library"
        assert by_id["rec1"].used_by

    def test_graphics_and_captions_are_alpha_overlays_on_their_own_tracks(self) -> None:
        spec = compiled().spec

        tracks = {t.track_id: t for t in spec.tracks}
        roles = {o.source_ref: tracks[o.track_id].role for o in spec.overlays}
        assert roles["g1"] is TrackRole.GRAPHICS
        assert roles["cap:1"] is TrackRole.CAPTIONS
        assert all(o.execution is Execution.RENDERED for o in spec.overlays)

    def test_the_creator_identity_is_a_reference_not_a_copy(self) -> None:
        spec = compiled().spec

        assert spec.creator.style_fingerprint == f.STYLE_FP
        assert spec.creator.identity_id == "mesc" and spec.creator.identity_version == 2
        assert ("graphic_kind", "lower_third") in spec.creator.presets


class TestDeterminism:
    def test_identical_inputs_give_identical_specifications(self) -> None:
        a, b = compiled().spec, compiled().spec

        assert a.compilation.spec_hash == b.compilation.spec_hash
        assert a == b

    def test_the_hash_ignores_the_clock_and_the_version_number(self) -> None:
        first = compile_timeline(f.inputs(), CompilerConfig(), meta=f.meta(1))
        later = compile_timeline(f.inputs(), CompilerConfig(), meta=f.meta(7, "prev"))

        assert first.spec.compilation.spec_hash == later.spec.compilation.spec_hash

    def test_a_changed_input_changes_the_hash(self) -> None:
        base = compiled().spec
        moved = replace(
            f.source(),
            clips=(
                *f.source().clips[:2],
                f.clip("pC", "broll1", 0, 1, 6, layer=1),
                *f.source().clips[3:],
            ),
        )

        other = compile_timeline(f.inputs(source=moved), CompilerConfig(), meta=f.meta()).spec

        assert other.compilation.spec_hash != base.compilation.spec_hash


class TestMissingAndInvalid:
    def test_a_missing_source_asset_is_critical(self) -> None:
        assets = dict(f.assets())
        del assets["rec1"]

        result = compiled(assets=assets)

        assert "missing_source_asset" in codes(result, IssueClass.CRITICAL)

    def test_a_source_range_beyond_the_media_is_critical(self) -> None:
        assets = dict(f.assets())
        assets["rec1"] = f.asset("rec1", duration=12)

        assert "source_range_beyond_media" in codes(compiled(assets=assets), IssueClass.CRITICAL)

    def test_an_unavailable_file_is_critical(self) -> None:
        assets = dict(f.assets())
        assets["rec1"] = f.asset("rec1", available=False)

        assert "source_file_unavailable" in codes(compiled(assets=assets), IssueClass.CRITICAL)

    def test_overlapping_main_clips_are_critical(self) -> None:
        src = f.source()
        clips = (f.clip("pA", "rec1", 10, 14, 0), f.clip("pB", "rec1", 20, 24, 3), *src.clips[3:])

        result = compiled(source=replace(src, clips=clips))

        assert "overlapping_primary_clips" in codes(result, IssueClass.CRITICAL)

    def test_a_gap_on_the_main_track_is_a_warning(self) -> None:
        src = f.source()
        clips = (f.clip("pA", "rec1", 10, 14, 0), f.clip("pB", "rec1", 20, 24, 5), *src.clips[3:])

        assert "gap_on_primary" in codes(
            compiled(source=replace(src, clips=clips)), IssueClass.WARNING
        )

    def test_a_rendered_overlay_shorter_than_the_item_is_critical(self) -> None:
        assets = dict(f.assets())
        assets["gfx1"] = f.asset("gfx1", duration=1, audio=False, original="rec1")

        assert "overlay_beyond_render" in codes(compiled(assets=assets), IssueClass.CRITICAL)

    def test_a_failed_upstream_plan_is_never_compiled_as_success(self) -> None:
        result = compiled(undelivered=(("captions", "failed: model missing"),))

        assert "upstream_not_delivered" in codes(result, IssueClass.CRITICAL)
        assert any(
            d.state.value == "requested" and d.owner == "caption_engine"
            for d in result.spec.dependencies
        )

    def test_an_audio_sound_out_of_sync_with_its_picture_is_critical(self) -> None:
        src = f.source()
        shifted = f.clip("aV", "rec1", 10, 14, 1, picture=False, linked="pA")
        clips = (*src.clips[:3], shifted, src.clips[4])

        assert "audio_video_out_of_sync" in codes(
            compiled(source=replace(src, clips=clips)), IssueClass.CRITICAL
        )


class TestIdentity:
    def test_a_plan_made_with_another_style_blocks_the_timeline(self) -> None:
        other = replace(f.captions(), stamp=f.stamp("cp", style="0" * 64))

        result = compiled(captions=other)

        assert "style_mismatch" in codes(result, IssueClass.CRITICAL)

    def test_a_style_mismatch_can_be_allowed_and_is_then_a_warning(self) -> None:
        other = replace(f.captions(), stamp=f.stamp("cp", style="0" * 64))

        result = compile_timeline(
            f.inputs(captions=other), CompilerConfig(allow_style_mismatch=True), meta=f.meta()
        )

        assert "style_mismatch" in codes(result, IssueClass.WARNING)

    def test_a_plan_for_another_edit_blocks_the_timeline(self) -> None:
        other = replace(
            f.graphics(), stamp=f.stamp("gp", edit=EditRef(edit_plan_id="edit-9", plan_version=1))
        )

        assert "plan_for_another_edit" in codes(compiled(graphics=other), IssueClass.CRITICAL)

    def test_a_locked_rule_break_is_a_creative_conflict_and_the_item_stays(self) -> None:
        drift = (
            (
                f.DriftFact(
                    severity="error", code="zone", message="graphic outside its zone", item_id="g1"
                ),
            )
            if hasattr(f, "DriftFact")
            else ()
        )
        from media_house.modules.edit_plan_compiler.domain.facts import DriftFact

        style = f.style(
            drift=(DriftFact(severity="error", code="zone", message="outside zone", item_id="g1"),)
        )
        result = compiled(style=style)

        assert "identity:zone" in codes(result, IssueClass.CREATIVE_CONFLICT)
        assert any(o.source_ref == "g1" for o in result.spec.overlays)
        assert result.spec.state is ResultState.PARTIAL
        assert drift is not None

    def test_the_violating_item_can_be_left_out_by_explicit_config(self) -> None:
        from media_house.modules.edit_plan_compiler.domain.facts import DriftFact

        style = f.style(
            drift=(DriftFact(severity="error", code="zone", message="outside zone", item_id="g1"),)
        )

        result = compile_timeline(
            f.inputs(style=style), CompilerConfig(drop_identity_violations=True), meta=f.meta()
        )

        assert not any(o.source_ref == "g1" for o in result.spec.overlays)
        assert "omitted_for_identity" in codes(result, IssueClass.AUTO_RESOLVED)

    def test_a_missing_font_is_reported(self) -> None:
        from media_house.modules.edit_plan_compiler.domain.facts import FontFact

        style = f.style(
            fonts=(
                FontFact(key="display", family="Anton", weight=400, sha256="aa", available=False),
            )
        )

        assert "font_unavailable" in codes(compiled(style=style), IssueClass.WARNING)


class TestJumpCutPunchIn:
    def test_every_other_back_to_back_talking_clip_gets_a_static_tighter_window(self) -> None:
        spec = compile_timeline(
            f.inputs(vfx=None), CompilerConfig(jump_cut_zoom=1.12), meta=f.meta()
        ).spec

        by_clip = {v.clip_id: v for v in spec.video}
        assert by_clip["pA"].reframe == ()
        (window,) = by_clip["pB"].reframe
        (key,) = window.keys
        assert window.strategy == "punch_in"
        assert key.w == key.h == round(1 / 1.12, 6)
        assert key.x == round((1 - 1 / 1.12) / 2, 6)
        assert by_clip["pC"].reframe == ()  # cover footage keeps its framing

    def test_off_by_default(self) -> None:
        assert all(v.reframe == () for v in compiled(vfx=None).spec.video)
