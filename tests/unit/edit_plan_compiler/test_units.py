"""Transitions, VFX, reframing, derivatives and frame rates of the compiler."""

from dataclasses import replace

from media_house.modules.edit_plan_compiler.domain.engine import compile_timeline
from media_house.modules.edit_plan_compiler.domain.facts import (
    AssetFacts,
    CropFact,
    DerivativeFact,
    EffectFact,
    FramingRef,
    ReframeFacts,
    SourceBridge,
)
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.edit_plan_compiler.domain.values import (
    AssetRelation,
    DependencyState,
    DerivativePolicy,
    Execution,
    IssueClass,
    ItemOrigin,
    TrackRole,
)
from tests.unit.edit_plan_compiler import fixtures as f


def build(config: CompilerConfig | None = None, **kw: object):  # type: ignore[no-untyped-def]
    return compile_timeline(f.inputs(**kw), config or CompilerConfig(), meta=f.meta())


def codes(result, klass: IssueClass) -> set[str]:  # type: ignore[no-untyped-def]
    return {i.code for i in (*result.spec.issues, *result.critical) if i.klass is klass}


class TestTransitions:
    def test_a_transition_with_its_handles_runs_natively_and_links_both_clips(self) -> None:
        spec = build(vfx=None).spec

        [t] = spec.transitions
        assert t.execution is Execution.NATIVE and t.handles.sufficient
        by_id = {v.item_id: v for v in spec.video}
        assert by_id["v:pA"].transition_out == t.transition_id
        assert by_id["v:pB"].transition_in == t.transition_id
        assert (t.start_frame, t.end_frame, t.duration_frames) == (98, 102, 4)

    def test_a_baked_clip_holds_no_footage_past_its_own_cut(self) -> None:
        """The rendered VFX file ends exactly where the clip does, so a dissolve cannot read a
        handle from it: the cut is made a hard one and the reason is said."""
        result = build()  # pA is baked in the default fixture

        [t] = result.spec.transitions
        assert t.kind == "hard_cut" and t.adjusted_by_compiler and t.wanted_kind == "cross_dissolve"
        assert t.handles.available_out == 0 and not t.handles.sufficient
        assert "transition_not_executable" in codes(result, IssueClass.AUTO_RESOLVED)

    def test_the_recording_range_of_a_baked_clip_is_kept(self) -> None:
        by_id = {v.item_id: v for v in build().spec.video}

        pa = by_id["v:pA"]
        assert (pa.source_start, pa.source_end) == (f.r(0), f.r(4))  # inside the baked file
        assert (pa.original_start, pa.original_end) == (f.r(10), f.r(14))  # on the recording

    def test_missing_handles_fall_back_to_a_hard_cut_and_say_so(self) -> None:
        plan = f.transitions(f.transition(handles=(5, 5), available=(2, 200)))

        result = build(transitions=plan)

        [t] = result.spec.transitions
        assert t.kind == "hard_cut" and t.adjusted_by_compiler and t.wanted_kind == "cross_dissolve"
        assert t.execution is Execution.FALLBACK and t.duration_frames == 0
        assert "transition_not_executable" in codes(result, IssueClass.AUTO_RESOLVED)

    def test_handles_are_checked_against_the_files_not_only_the_plan(self) -> None:
        assets = dict(f.assets())
        assets["rec1"] = f.asset("rec1", duration=14)  # nothing after pA's out point at 14 s
        plan = f.transitions(f.transition(handles=(5, 5), available=(500, 500)))

        result = build(assets=assets, transitions=plan)

        assert result.spec.transitions[0].kind == "hard_cut"

    def test_a_required_transition_that_cannot_run_is_a_creative_conflict(self) -> None:
        plan = f.transitions(f.transition(handles=(5, 5), available=(0, 0), status="requested"))

        result = build(transitions=plan)

        assert "transition_not_executable" in codes(result, IssueClass.CREATIVE_CONFLICT)
        assert result.spec.transitions[0].wanted_kind == "cross_dissolve"

    def test_a_graphic_transition_without_its_overlay_cannot_run(self) -> None:
        plan = f.transitions(f.transition("wipe", family="graphic", handles=(0, 0)))

        result = build(transitions=plan)

        assert result.spec.transitions[0].kind == "hard_cut"
        assert "transition_not_executable" in codes(result, IssueClass.AUTO_RESOLVED)

    def test_a_rendered_overlay_goes_on_the_effects_track(self) -> None:
        assets = dict(f.assets())
        assets["wipe-file"] = f.asset("wipe-file", duration=2, audio=False, original="rec1")
        plan = f.transitions(
            f.transition("wipe", family="graphic", handles=(0, 0), overlay="wipe-file")
        )

        spec = build(assets=assets, transitions=plan).spec

        [t] = spec.transitions
        assert t.execution is Execution.RENDERED and t.overlay_item_id
        overlay = next(o for o in spec.overlays if o.item_id == t.overlay_item_id)
        assert (
            next(x for x in spec.tracks if x.track_id == overlay.track_id).role is TrackRole.EFFECTS
        )

    def test_the_audio_crossfade_targets_the_two_dialogue_items(self) -> None:
        spec = build().spec

        x = next(a for a in spec.automation if a.kind == "crossfade")
        assert x.target_item_ids == ("a:aV", "a:aW") and x.curve == "equal_power"
        assert x.sample_rate == 48_000

    def test_a_crossfade_longer_than_the_sources_handle_is_shortened(self) -> None:
        assets = dict(f.assets())
        assets["rec1"] = f.asset("rec1", duration=14)  # no audio after aV's end
        plan = f.transitions(f.transition("hard_cut", family="cut", handles=(0, 0)))

        result = build(assets=assets, transitions=plan)

        assert "audio_handle_short" in codes(result, IssueClass.AUTO_RESOLVED)

    def test_an_impossibly_long_transition_becomes_a_cut(self) -> None:
        t = replace(f.transition(handles=(0, 0)), duration_frames=999)

        result = build(transitions=f.transitions(t))

        assert result.spec.transitions[0].kind == "hard_cut"


class TestVfx:
    def test_a_rendered_treatment_places_the_baked_clip_and_keeps_the_retime(self) -> None:
        spec = build().spec

        v = next(v for v in spec.video if v.clip_id == "pA")
        assert v.asset_id == "vfx-out" and v.original_asset_id == "rec1"
        assert v.origin is ItemOrigin.VFX and v.speed.numerator == 1
        assert v.retime is not None and v.retime.baked
        assert [s.kind for s in v.retime.segments] == ["normal", "slow"]
        assert spec.effects[0].execution is Execution.BAKED

    def test_an_unrendered_treatment_falls_back_to_the_original_footage(self) -> None:
        result = build(vfx=f.vfx("planned"))

        v = next(v for v in result.spec.video if v.clip_id == "pA")
        assert v.asset_id == "rec1" and v.origin is ItemOrigin.EDIT
        assert result.spec.effects[0].execution is Execution.FALLBACK
        assert "vfx_not_rendered" in codes(result, IssueClass.WARNING)
        request = next(d for d in result.spec.dependencies if d.owner == "vfx_engine")
        assert request.state is DependencyState.REQUESTED and request.request == "vfx"

    def test_a_rendered_clip_with_too_few_frames_blocks_the_timeline(self) -> None:
        plan = f.vfx()
        short = replace(plan.treatments[0], output_frames=10)

        result = build(vfx=replace(plan, treatments=(short,)))

        assert "vfx_clip_too_short" in codes(result, IssueClass.CRITICAL)

    def test_two_overlapping_time_effects_are_a_creative_conflict_and_both_stay(self) -> None:
        plan = f.vfx()
        base = plan.treatments[0]
        second = replace(base.effects[0], effect_id="e2", effect_type="speed_ramp")
        both = replace(base, effects=(base.effects[0], second))

        result = build(vfx=replace(plan, treatments=(both,)))

        assert "conflicting_time_effects" in codes(result, IssueClass.CREATIVE_CONFLICT)
        assert {e.effect_id for e in result.spec.effects} == {"e1", "e2"}

    def test_a_rejected_effect_is_kept_as_unavailable_without_a_warning(self) -> None:
        plan = f.vfx()
        base = plan.treatments[0]
        rejected = replace(base.effects[0], status="rejected")

        result = build(vfx=replace(plan, treatments=(replace(base, effects=(rejected,)),)))

        assert result.spec.effects[0].execution is Execution.UNAVAILABLE
        assert "effect_not_applied" not in codes(result, IssueClass.WARNING)

    def test_a_mask_travels_with_its_effect(self) -> None:
        plan = f.vfx()
        base = plan.treatments[0]
        mask = (
            f.MaskFact(mask_id="m1", kind="tracked", keyframes=((f.r(10), 0.1, 0.1, 0.5, 0.5),))
            if hasattr(f, "MaskFact")
            else None
        )
        from media_house.modules.edit_plan_compiler.domain.facts import MaskFact

        mask = MaskFact(
            mask_id="m1",
            kind="tracked",
            keyframes=((f.r(10), 0.1, 0.1, 0.5, 0.5),),
            track_refs=("a/t1",),
        )
        blurred = EffectFact(
            effect_id="e9",
            effect_type="privacy_blur",
            status="rendered",
            preset="p",
            mode="",
            parameters=(),
            intensity=1,
            timeline_start=f.r(0),
            timeline_end=f.r(1),
            start_frame=0,
            end_frame=25,
            backend="ffmpeg",
            fallback="original footage",
            masks=(mask,),
        )

        spec = build(vfx=replace(plan, treatments=(replace(base, effects=(blurred,)),))).spec

        assert spec.effects[0].masks[0].track_refs == ("a/t1",)
        assert mask is not None


def framing(*, executable: bool = True, target: str = "16:9", rec: str = "rec-1") -> FramingRef:
    return FramingRef(
        recommendation_id=rec,
        analysis_id="an-1",
        target=target,
        strategy="follow",
        executable=executable,
        source_start=f.r(10),
        source_end=f.r(14),
    )


def with_framing(ref: FramingRef):  # type: ignore[no-untyped-def]
    src = f.source()
    return replace(src, clips=(f.clip("pA", "rec1", 10, 14, 0, framing=(ref,)), *src.clips[1:]))


def crop_facts(rec: str = "rec-1") -> dict[str, ReframeFacts]:
    return {
        rec: ReframeFacts(
            recommendation_id=rec,
            analysis_id="an-1",
            target="16:9",
            strategy="follow",
            executable=True,
            keyframes=(CropFact(time=f.r(10), x=0.1, y=0.0, w=0.8, h=1.0, interpolation="hold"),),
        )
    }


class TestReframing:
    def test_an_executable_crop_is_resolved_into_keyframes(self) -> None:
        spec = build(source=with_framing(framing()), reframing=crop_facts()).spec

        v = next(v for v in spec.video if v.clip_id == "pA")
        [r] = v.reframe
        assert r.recommendation_id == "rec-1" and r.keys[0].w == 0.8

    def test_a_crop_that_is_not_in_the_analysis_is_reported_and_requested(self) -> None:
        result = build(source=with_framing(framing()), reframing={})

        assert "framing_unresolved" in codes(result, IssueClass.WARNING)
        assert any(d.owner == "reframing_intelligence" for d in result.spec.dependencies)

    def test_a_crop_that_cannot_be_applied_keeps_the_original_framing(self) -> None:
        result = build(source=with_framing(framing(executable=False)))

        v = next(v for v in result.spec.video if v.clip_id == "pA")
        assert v.reframe == () and "framing_not_executable" in codes(result, IssueClass.WARNING)

    def test_a_crop_made_for_another_shape_is_a_creative_conflict_and_is_kept(self) -> None:
        facts = crop_facts()
        result = build(source=with_framing(framing(target="9:16")), reframing=facts)

        assert "framing_target_mismatch" in codes(result, IssueClass.CREATIVE_CONFLICT)
        assert next(v for v in result.spec.video if v.clip_id == "pA").reframe

    def test_two_crops_over_the_same_footage_conflict(self) -> None:
        src = f.source()
        a, b = framing(rec="rec-1"), framing(rec="rec-2")
        clip = f.clip("pA", "rec1", 10, 14, 0, framing=(a, b))
        facts = {**crop_facts("rec-1"), **crop_facts("rec-2")}

        result = build(source=replace(src, clips=(clip, *src.clips[1:])), reframing=facts)

        assert "conflicting_framing" in codes(result, IssueClass.CREATIVE_CONFLICT)

    def test_a_crop_and_a_punch_in_both_change_the_framing(self) -> None:
        plan = f.vfx()
        base = plan.treatments[0]
        punch = replace(base.effects[0], effect_id="e5", effect_type="punch_in")

        result = build(
            source=with_framing(framing()),
            reframing=crop_facts(),
            vfx=replace(plan, treatments=(replace(base, effects=(punch,)),)),
        )

        assert "reframe_and_punch_in" in codes(result, IssueClass.CREATIVE_CONFLICT)


class TestDerivatives:
    def improved(self) -> dict[str, AssetFacts]:
        assets = dict(f.assets())
        assets["rec1"] = f.asset(
            "rec1",
            derivatives=(DerivativeFact(asset_id="rec1-clean", operation="audio_improvement"),),
        )
        assets["rec1-clean"] = f.asset(
            "rec1-clean", original="rec1", operation="audio_improvement", video=False, media="audio"
        )
        return assets

    def test_the_recording_is_used_by_default_even_when_an_improved_audio_exists(self) -> None:
        spec = build(assets=self.improved()).spec

        assert {a.asset_id for a in spec.audio if a.origin is ItemOrigin.EDIT} == {"rec1"}

    def test_the_improved_audio_is_used_when_the_config_asks_and_traces_to_the_recording(
        self,
    ) -> None:
        spec = build(
            CompilerConfig(derivative_policy=DerivativePolicy.PREFER_IMPROVED),
            assets=self.improved(),
        ).spec

        edit_audio = [a for a in spec.audio if a.origin is ItemOrigin.EDIT]
        assert {a.asset_id for a in edit_audio} == {"rec1-clean"}
        assert {a.original_asset_id for a in edit_audio} == {"rec1"}
        use = next(a for a in spec.assets if a.asset_id == "rec1-clean")
        assert use.relation is AssetRelation.IMPROVED_AUDIO
        assert next(v for v in spec.video if v.clip_id == "pB").asset_id == "rec1"

    def test_an_unusable_improved_version_falls_back_to_the_recording_with_a_warning(self) -> None:
        assets = self.improved()
        assets["rec1-clean"] = f.asset(
            "rec1-clean", original="rec1", operation="audio_improvement", available=False
        )

        result = build(
            CompilerConfig(derivative_policy=DerivativePolicy.PREFER_IMPROVED), assets=assets
        )

        assert "improved_version_unusable" in codes(result, IssueClass.WARNING)
        assert {a.asset_id for a in result.spec.audio if a.origin is ItemOrigin.EDIT} == {"rec1"}

    def test_an_edit_that_names_a_proxy_still_traces_to_the_recording(self) -> None:
        assets = dict(f.assets())
        assets["rec1"] = f.asset("rec1", original="orig-rec", operation="proxy")

        spec = build(assets=assets).spec

        assert next(v for v in spec.video if v.clip_id == "pB").original_asset_id == "orig-rec"


class TestFrameRates:
    def test_a_different_timeline_rate_resnaps_frames_from_the_exact_times(self) -> None:
        spec = build(CompilerConfig(timeline_frame_rate=f.r(30))).spec

        b = next(v for v in spec.video if v.clip_id == "pB")
        assert (b.start_frame, b.end_frame) == (120, 240)
        assert spec.timebase.duration_frames == 240

    def test_a_source_at_another_rate_is_conformed_and_flagged_once(self) -> None:
        assets = dict(f.assets())
        assets["rec1"] = f.asset("rec1", fps=f.r(30000, 1001))

        result = build(assets=assets)

        flagged = [i for i in result.spec.issues if i.code == "frame_rate_conversion"]
        assert len(flagged) == 1

    def test_variable_frame_rate_is_exact_in_seconds_and_flagged(self) -> None:
        assets = dict(f.assets())
        assets["rec1"] = f.asset("rec1", variable=True)

        result = build(assets=assets)

        v = next(v for v in result.spec.video if v.clip_id == "pB")
        assert v.source_variable_rate and v.source_start_frame is None
        assert "variable_frame_rate_source" in codes(result, IssueClass.WARNING)

    def test_a_second_camera_is_just_another_recording(self) -> None:
        src = f.source()
        clips = (*src.clips[:2], f.clip("pC", "cam2", 0, 1, 5, layer=1), *src.clips[3:])
        assets = dict(f.assets())
        assets["cam2"] = f.asset("cam2", fps=f.r(30), audio=False)

        spec = build(source=replace(src, clips=clips), assets=assets).spec

        assert {a.asset_id for a in spec.assets if a.relation is AssetRelation.ORIGINAL} == {
            "rec1",
            "cam2",
        }


class TestBridgesAndNoSpeech:
    def test_a_declared_bridge_the_placements_do_not_realise_is_reported(self) -> None:
        src = replace(
            f.source(),
            bridges=(
                SourceBridge(bridge_id="b1", kind="j_cut", audio_at=f.r(3), picture_at=f.r(4)),
            ),
        )

        assert "bridge_not_realised" in codes(build(source=src), IssueClass.WARNING)

    def test_a_bridge_the_placements_realise_is_not_reported(self) -> None:
        src = replace(
            f.source(),
            bridges=(
                SourceBridge(bridge_id="b1", kind="j_cut", audio_at=f.r(4), picture_at=f.r(4)),
            ),
        )

        assert "bridge_not_realised" not in codes(build(source=src), IssueClass.WARNING)

    def test_without_an_edit_the_recording_is_one_clip_with_its_own_sound(self) -> None:
        src = replace(
            f.source(),
            edit=None,
            project_id=None,
            clips=(f.clip("clip:rec1", "rec1", 0, 8, 0, section=None),),
            sections=(),
        )
        inputs = f.inputs(
            source=src,
            graphics=None,
            captions=None,
            transitions=None,
            vfx=None,
            sound=None,
        )

        result = compile_timeline(inputs, CompilerConfig(), meta=f.meta())

        assert result.critical == () and result.spec.edit is None
        [video] = result.spec.video
        [audio] = result.spec.audio
        assert audio.linked_item_id == video.item_id and audio.track_id == "A1"
        assert (video.start_frame, video.end_frame) == (0, 200)

    def test_a_recording_without_audio_gets_no_audio_item(self) -> None:
        src = replace(f.source(), edit=None, clips=(f.clip("clip:rec1", "rec1", 0, 8, 0),))
        assets = dict(f.assets())
        assets["rec1"] = f.asset("rec1", audio=False)

        spec = compile_timeline(
            f.inputs(
                source=src,
                assets=assets,
                graphics=None,
                captions=None,
                transitions=None,
                vfx=None,
                sound=None,
            ),
            CompilerConfig(),
            meta=f.meta(),
        ).spec

        assert spec.audio == ()


class TestSoundtrack:
    def test_music_and_effects_land_on_their_tracks_with_exact_samples(self) -> None:
        spec = build().spec

        tracks = {t.track_id: t.role for t in spec.tracks}
        music = next(a for a in spec.audio if a.source_ref == "seg1")
        assert tracks[music.track_id] is TrackRole.MUSIC and music.gain_db == -18.0
        assert (music.start_sample, music.end_sample) == (0, 384_000)
        sfx = next(a for a in spec.audio if a.source_ref == "sfx:1")
        assert tracks[sfx.track_id] is TrackRole.SFX and sfx.start_sample == 187_200

    def test_ducking_names_the_music_it_lowers(self) -> None:
        duck = next(a for a in build().spec.automation if a.kind == "duck")

        assert duck.target_item_ids == ("a:music:seg1",) and duck.depth_db == -10.0

    def test_music_beyond_its_file_blocks_the_timeline(self) -> None:
        assets = dict(f.assets())
        assets["song1"] = f.asset("song1", duration=8, video=False, media="audio")

        assert "source_range_beyond_media" in codes(build(assets=assets), IssueClass.CRITICAL)

    def test_a_missing_sound_is_reported_and_credits_travel(self) -> None:
        from media_house.modules.edit_plan_compiler.domain.facts import MissingSoundFact

        miss = MissingSoundFact(
            recommendation_id="miss:1",
            kind="music",
            purpose="hook",
            priority="recommended",
            timeline_start=f.r(0),
        )

        result = build(sound=replace(f.sound(), missing=(miss,)))

        assert "sound_missing" in codes(result, IssueClass.WARNING)
        assert result.spec.credits == "Song by X"

    def test_a_sound_plan_that_disagrees_with_the_edit_is_a_creative_conflict(self) -> None:
        from media_house.modules.edit_plan_compiler.domain.facts import OriginalAudioFact

        wrong = OriginalAudioFact(
            clip_id="aV",
            asset_id="rec1",
            timeline_start=f.r(0),
            timeline_end=f.r(4),
            mode="muted_by_edit",
            music_over_it=True,
        )

        result = build(sound=replace(f.sound(), original_audio=(wrong,)))

        assert "original_audio_conflict" in codes(result, IssueClass.CREATIVE_CONFLICT)


class TestOverlays:
    def test_an_unrendered_caption_stays_in_the_subtitles_and_is_requested(self) -> None:
        plan = f.captions()
        seg = replace(plan.segments[0], render_status="not_available", asset_id=None)

        result = build(captions=replace(plan, segments=(seg, plan.segments[1])))

        assert "caption_not_rendered" in codes(result, IssueClass.WARNING)
        assert result.spec.subtitles is not None and "hello there" in result.spec.subtitles.srt
        assert {o.source_ref for o in result.spec.overlays if o.kind == "caption"} == {"cap:2"}

    def test_requiring_rendered_overlays_turns_the_same_gap_critical(self) -> None:
        plan = f.captions()
        seg = replace(plan.segments[0], render_status="failed", asset_id=None)

        result = build(
            CompilerConfig(require_rendered_overlays=True),
            captions=replace(plan, segments=(seg, plan.segments[1])),
        )

        assert "caption_not_rendered" in codes(result, IssueClass.CRITICAL)

    def test_disabled_captions_place_nothing_and_raise_nothing(self) -> None:
        plan = replace(f.captions(), mode="disabled")

        result = build(captions=plan)

        assert (
            not [o for o in result.spec.overlays if o.kind == "caption"] and result.critical == ()
        )

    def test_a_graphic_that_was_not_drawn_is_left_out_and_requested(self) -> None:
        plan = f.graphics()
        g = replace(plan.graphics[0], status="not_available", asset_id=None)

        result = build(graphics=replace(plan, graphics=(g,)))

        assert not [o for o in result.spec.overlays if o.kind == "lower_third"]
        assert any(d.owner == "motion_graphics_engine" for d in result.spec.dependencies)

    def test_two_graphics_in_the_same_zone_at_once_conflict(self) -> None:
        plan = f.graphics()
        twin = replace(plan.graphics[0], graphic_id="g2", timeline_start=f.r(2))

        result = build(graphics=replace(plan, graphics=(plan.graphics[0], twin)))

        assert "zone_overlap" in codes(result, IssueClass.CREATIVE_CONFLICT)

    def test_overlapping_graphics_use_a_second_lane_not_the_same_track(self) -> None:
        plan = f.graphics()
        other = replace(plan.graphics[0], graphic_id="g2", timeline_start=f.r(2), zone="top_title")

        spec = build(graphics=replace(plan, graphics=(plan.graphics[0], other))).spec

        tracks = {o.source_ref: o.track_id for o in spec.overlays if o.kind == "lower_third"}
        assert tracks["g1"] != tracks["g2"]
        assert sum(t.role is TrackRole.GRAPHICS for t in spec.tracks) == 2

    def test_the_end_screen_bookend_extends_the_timeline(self) -> None:
        plan = replace(f.graphics(), timeline_end=f.r(10))

        spec = build(graphics=plan).spec

        assert spec.timebase.duration_frames == 250
