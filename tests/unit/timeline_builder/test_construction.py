"""The built timeline is what the specification describes, organised for a person to open."""

from dataclasses import replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.facts import (
    CropFact,
    FramingRef,
    ReframeFacts,
)
from media_house.modules.timeline_builder.domain.model import BuiltTimeline
from media_house.modules.timeline_builder.domain.values import (
    BlendMode,
    BuildMode,
    BuildStatus,
    ChainStage,
    ClipKind,
    ClipState,
    Execution,
    Provenance,
    Role,
    Severity,
    TrackKind,
    TransitionMode,
)
from tests.unit.edit_plan_compiler import fixtures as cf
from tests.unit.timeline_builder import fixtures as f


def video(t: BuiltTimeline, clip_id: str):  # type: ignore[no-untyped-def]
    clip = t.video_clip(clip_id)
    assert clip is not None, clip_id
    return clip


def audio(t: BuiltTimeline, clip_id: str):  # type: ignore[no-untyped-def]
    clip = t.audio_clip(clip_id)
    assert clip is not None, clip_id
    return clip


class TestSettingsAndTracks:
    def test_the_timeline_is_configured_from_the_specification(self) -> None:
        t = f.built()

        s = t.settings
        assert (s.width, s.height, s.aspect) == (1920, 1080, (16, 9))
        assert s.frame_rate == Rational(25, 1) and not s.frame_rate_assumed
        assert s.sample_rate == 48_000 and s.audio_channels == "stereo"
        assert (s.duration, s.duration_frames) == (Rational(8, 1), 200)
        assert (s.color.profile, s.color.space, s.color.transfer) == ("rec709", "bt709", "bt709")
        assert t.timeline_id and t.anchor_asset_id == "rec1" and t.project_id == "proj-1"
        assert t.edit is not None and t.edit.edit_plan_id == "edit-1"

    def test_a_vertical_target_keeps_its_own_shape(self) -> None:
        facts = f.facts_of()
        timebase = replace(facts.timebase, width=1080, height=1920)

        t = f.built(replace(facts, timebase=timebase))

        assert t.settings.aspect == (9, 16)

    def test_every_specified_track_exists_with_a_readable_name_and_group(self) -> None:
        t = f.built()

        assert [x.track_id for x in t.tracks] == [
            "V1", "V2", "V3", "V4", "V5", "A1", "A2", "A3", "A4", "A5",
        ]  # fmt: skip
        v3 = t.track("V3")
        assert v3 is not None
        assert v3.name == "V3 Graphics" and v3.group == "Video · Graphics"
        assert v3.blend is BlendMode.ALPHA_OVER and v3.kind is TrackKind.VIDEO
        a3 = t.track("A3")
        assert a3 is not None and a3.role is Role.MUSIC

    def test_clips_sit_on_the_tracks_the_specification_names_in_time_order(self) -> None:
        t = f.built()

        v1 = t.track("V1")
        assert v1 is not None
        assert [c.clip_id for c in v1.video] == ["v:pA", "v:pB"]
        assert [c.clip_id for c in t.track("V2").video] == ["v:pC"]  # type: ignore[union-attr]
        assert [c.clip_id for c in t.track("A1").audio] == ["a:aV", "a:aW"]  # type: ignore[union-attr]
        assert [c.clip_id for c in t.track("A3").audio] == ["a:music:seg1"]  # type: ignore[union-attr]

    def test_the_counts_match_the_specification(self) -> None:
        spec = f.compiled()

        t = f.built(f.facts_of())

        c = t.counts
        assert c.video_tracks == 5 and c.audio_tracks == 5
        assert c.video_clips == len(spec.video) + len(spec.overlays)
        assert c.audio_clips == len(spec.audio)
        assert c.transitions == len(spec.transitions)


class TestPictureClips:
    def test_a_clip_keeps_its_exact_times_frames_and_source_range(self) -> None:
        t = f.built()

        b = video(t, "v:pB")
        assert (b.timeline_start, b.timeline_end) == (Rational(4, 1), Rational(8, 1))
        assert (b.start_frame, b.end_frame, b.frames) == (100, 200, 100)
        assert b.source is not None
        assert (b.source.source_in, b.source.source_out) == (Rational(20, 1), Rational(24, 1))
        assert b.speed == Rational(1, 1) and b.kind is ClipKind.PRIMARY
        assert b.provenance is Provenance.AI and b.origin_ref == "v:pB"

    def test_one_recording_is_one_source_however_often_it_is_played(self) -> None:
        t = f.built()

        rec = t.source("rec1")
        assert rec is not None
        assert {"v:pA", "v:pB", "a:aV", "a:aW"} <= set(rec.used_by)
        assert [s.source_id for s in t.sources].count("rec1") == 1
        assert rec.original_asset_id == "rec1" and rec.relation == "original"
        first, second = video(t, "v:pA").source, video(t, "v:pB").source
        assert first is not None and second is not None
        assert first.source_id == second.source_id

    def test_a_baked_clip_points_at_its_render_and_traces_to_the_recording(self) -> None:
        t = f.built(f.facts_of(vfx=cf.vfx()))

        a = video(t, "v:pA")
        assert a.kind is ClipKind.VFX
        assert a.source is not None and a.source.source_id == "vfx-out"
        assert a.source.original_asset_id == "rec1"
        assert (a.source.original_in, a.source.original_out) == (Rational(10, 1), Rational(14, 1))
        media = t.source("vfx-out")
        assert media is not None and media.relation == "rendered"
        assert media.original_asset_id == "rec1"

    def test_the_processing_chain_has_a_fixed_order_and_bakes_apply_nothing_twice(self) -> None:
        t = f.built(f.facts_of(vfx=cf.vfx()))

        a = video(t, "v:pA")
        assert [s.stage for s in a.chain] == [
            ChainStage.TIME_MAP,
            ChainStage.FIT,
            ChainStage.EFFECT,
            ChainStage.COMPOSITE,
        ]
        assert [s.order for s in a.chain] == [1, 2, 3, 4]
        by_stage = {s.stage: s for s in a.chain}
        assert not by_stage[ChainStage.TIME_MAP].applied  # the render already shows the retime
        assert not by_stage[ChainStage.EFFECT].applied
        assert a.time_map is not None and not a.time_map.applied
        assert [seg.kind for seg in a.time_map.segments] == ["normal", "slow"]
        [effect] = a.effects
        assert effect.execution is Execution.BAKED and not effect.applied

    def test_a_plain_clip_is_fit_then_composited(self) -> None:
        b = video(f.built(), "v:pB")

        assert [s.stage for s in b.chain] == [ChainStage.FIT, ChainStage.COMPOSITE]
        assert b.transform is not None and b.transform.fit.value == "contain"
        assert (b.opacity, b.blend) == (1.0, BlendMode.NORMAL)

    def test_cover_footage_is_a_cover_clip_on_its_own_track(self) -> None:
        c = video(f.built(), "v:pC")

        assert c.kind is ClipKind.COVER and c.track_id == "V2"
        assert (c.start_frame, c.end_frame) == (125, 150)

    def test_names_say_where_the_clip_comes_from(self) -> None:
        t = f.built()

        name = video(t, "v:pB").name
        assert "Hook" in name and "rec1.mov" in name and "00:00:20.000" in name
        assert "Cover" in video(t, "v:pC").name
        assert not any(n.startswith(("Clip_", "Track_")) for n in (video(t, "v:pB").name,))


class TestReframing:
    def make(self) -> BuiltTimeline:
        framing = FramingRef(
            recommendation_id="r1",
            analysis_id="an1",
            target="16:9",
            strategy="static_crop",
            executable=True,
            source_start=Rational(20, 1),
            source_end=Rational(24, 1),
        )
        source = cf.source()
        clips = tuple(
            replace(c, framing=(framing,)) if c.clip_id == "pB" else c for c in source.clips
        )
        facts = f.facts_of(
            source=replace(source, clips=clips),
            reframing={
                "r1": ReframeFacts(
                    recommendation_id="r1",
                    analysis_id="an1",
                    target="16:9",
                    strategy="static_crop",
                    executable=True,
                    keyframes=(
                        CropFact(
                            time=Rational(20, 1), x=0.1, y=0.0, w=0.8, h=1.0, interpolation="hold"
                        ),
                        CropFact(
                            time=Rational(22, 1), x=0.2, y=0.0, w=0.8, h=1.0, interpolation="linear"
                        ),
                    ),
                )
            },
        )
        return f.built(facts)

    def test_the_crop_keeps_its_window_and_lands_on_the_timeline(self) -> None:
        b = video(self.make(), "v:pB")

        [crop] = b.crops
        assert (crop.recommendation_id, crop.analysis_id) == ("r1", "an1")
        assert [k.timeline_frame for k in crop.keys] == [100, 150]  # 20 s -> 4 s, 22 s -> 6 s
        assert [k.interpolation for k in crop.keys] == ["hold", "linear"]
        assert (crop.keys[0].x, crop.keys[0].w) == (0.1, 0.8)

    def test_the_crop_is_a_step_of_the_chain_between_fit_and_effects(self) -> None:
        b = video(self.make(), "v:pB")

        assert [s.stage for s in b.chain] == [ChainStage.FIT, ChainStage.CROP, ChainStage.COMPOSITE]
        assert b.chain[1].ref == b.crops[0].crop_id


class TestOverlaysAndCaptions:
    def test_a_graphic_is_an_alpha_clip_in_its_zone_with_its_design_reference(self) -> None:
        g = video(f.built(), "o:graphic:g1")

        assert g.kind is ClipKind.GRAPHIC and g.track_id == "V3"
        assert g.blend is BlendMode.ALPHA_OVER and g.zone == "lower_third"
        assert g.identity == (("graphic_kind", "lower_third"),)
        assert g.state is ClipState.RESOLVED
        assert g.source is not None and g.source.source_id == "gfx1"
        assert (g.start_frame, g.end_frame) == (25, 75)

    def test_captions_share_one_file_and_start_where_their_group_does(self) -> None:
        t = f.built()

        one, two = video(t, "o:caption:cap:1"), video(t, "o:caption:cap:2")
        assert one.source is not None and two.source is not None
        assert one.source.source_id == two.source.source_id == "cap-file"
        assert (one.source.asset_start_frame, two.source.asset_start_frame) == (0, 50)
        assert one.identity == (("caption_look", "word_group"),)
        assert one.zone == "caption_band" and one.track_id == "V4"

    def test_the_caption_text_is_kept_as_editable_cues(self) -> None:
        t = f.built()

        assert t.subtitles is not None
        assert [c.text for c in t.subtitles.cues] == ["hello there"]
        assert (t.subtitles.cues[0].start_frame, t.subtitles.cues[0].end_frame) == (0, 50)
        assert t.subtitles.language == "en" and t.subtitles.srt.startswith("1\n")
        assert "hello there" in video(t, "o:caption:cap:1").name


class TestTransitions:
    def test_a_dissolve_joins_two_named_clips_without_moving_either(self) -> None:
        t = f.built()

        [tr] = t.transitions
        assert tr.mode is TransitionMode.DISSOLVE and tr.supported
        assert (tr.from_clip, tr.to_clip) == ("v:pA", "v:pB")
        assert (tr.start_frame, tr.cut_frame, tr.end_frame, tr.duration_frames) == (98, 100, 102, 4)
        assert (tr.tail_frames, tr.head_frames) == (2, 2)
        assert tr.track_id == "V1" and tr.preset_id == "soft"
        assert video(t, "v:pA").transition_out == tr.transition_id
        assert video(t, "v:pB").transition_in == tr.transition_id
        assert (video(t, "v:pA").end_frame, video(t, "v:pB").start_frame) == (100, 100)
        assert t.settings.duration_frames == 200

    def test_a_cut_the_compiler_made_a_hard_one_stays_a_cut_and_remembers_the_wish(self) -> None:
        t = f.built(f.facts_of(vfx=cf.vfx()))  # pA is baked: no handle past its end

        [tr] = t.transitions
        assert tr.mode is TransitionMode.CUT and tr.supported
        assert tr.kind == "hard_cut" and tr.wanted_kind == "cross_dissolve"
        assert tr.adjusted_by_compiler and (tr.tail_frames, tr.head_frames) == (0, 0)

    def test_a_graphic_transition_is_an_overlay_clip_over_the_cut(self) -> None:
        plan = cf.transitions(cf.transition("wipe", family="graphic", overlay="wipe-file"))
        assets = dict(cf.assets())
        assets["wipe-file"] = cf.asset(
            "wipe-file", duration=1, audio=False, original="rec1", operation="motion_graphic_render"
        )

        t = f.built(f.facts_of(transitions=plan, assets=assets))

        [tr] = t.transitions
        assert tr.mode is TransitionMode.OVERLAY and tr.overlay_clip == "o:transition:cut:pA>pB"
        overlay = video(t, "o:transition:cut:pA>pB")
        assert overlay.kind is ClipKind.TRANSITION_OVERLAY and overlay.track_id == "V5"
        assert (tr.tail_frames, tr.head_frames) == (0, 0)

    def test_the_picture_sound_and_overlay_of_a_cut_are_grouped(self) -> None:
        t = f.built()

        group = next(g for g in t.groups if g.kind == "transition")
        assert {"v:pA", "v:pB", "cut:pA>pB"} <= set(group.members)
        assert any(g.group_id == "group:av:v:pA" for g in t.groups)


class TestSound:
    def test_sound_is_positioned_in_exact_samples_and_linked_to_its_picture(self) -> None:
        t = f.built()

        v = audio(t, "a:aV")
        assert v.role is Role.DIALOGUE and v.track_id == "A1"
        assert (v.start_sample, v.end_sample, v.samples) == (0, 192_000, 192_000)
        assert v.video_link == "v:pA" and video(t, "v:pA").audio_link == "a:aV"
        assert v.source is not None and v.source.source_in == Rational(10, 1)

    def test_music_keeps_its_gain_fades_and_ducking_instruction(self) -> None:
        t = f.built()

        m = audio(t, "a:music:seg1")
        assert (m.gain_db, m.fade_in, m.fade_out) == (-18.0, Rational(1, 1), Rational(2, 1))
        assert m.role is Role.MUSIC and m.video_link is None
        duck = next(x for x in t.mix if x.kind == "duck")
        assert duck.clip_ids == ("a:music:seg1",) and duck.depth_db == -10.0
        assert duck.mix_id in m.mix_ids

    def test_a_crossfade_instruction_is_attached_to_both_dialogue_clips(self) -> None:
        t = f.built()

        fade = next(x for x in t.mix if x.kind == "crossfade")
        assert set(fade.clip_ids) == {"a:aV", "a:aW"}
        assert fade.mix_id in audio(t, "a:aV").mix_ids and fade.mix_id in audio(t, "a:aW").mix_ids
        [tr] = t.transitions
        assert tr.mix_id == fade.mix_id

    def test_effects_sound_sits_on_its_own_track(self) -> None:
        sfx = audio(f.built(), "a:sfx:sfx:1")

        assert sfx.track_id == "A5" and sfx.role is Role.SFX and sfx.gain_db == -6.0


class TestOrganisation:
    def test_sections_and_their_markers(self) -> None:
        t = f.built()

        [section] = t.sections
        assert section.name == "Hook" and (section.start_frame, section.end_frame) == (0, 200)
        assert "v:pB" in section.clip_ids and "a:aW" in section.clip_ids
        marker = next(m for m in t.markers if m.kind == "section")
        assert (marker.frame, marker.duration_frames, marker.name) == (0, 200, "Hook")
        assert marker.provenance is Provenance.SYSTEM

    def test_markers_can_be_left_out(self) -> None:
        from media_house.modules.timeline_builder.domain.profile import BuilderConfig

        t = f.built(config=BuilderConfig(section_markers=False, review_markers=False))

        assert t.markers == ()

    def test_a_finding_with_a_time_becomes_a_review_marker(self) -> None:
        facts = f.facts_of()
        cut = f.change_transition(facts, "cut:pA>pB", kind="whip_pan", family="movement")

        t = f.built(cut)

        marker = next(m for m in t.markers if m.kind == "review")
        assert marker.severity is Severity.UNSUPPORTED and marker.ref == "cut:pA>pB"

    def test_every_element_has_a_unique_stable_id(self) -> None:
        a, b = f.built(), f.built()

        ids = a.element_ids()
        assert len(ids) == len(set(ids))
        assert ids == b.element_ids()


class TestIdentity:
    def test_the_creator_identity_is_a_reference_not_a_copy(self) -> None:
        t = f.built()

        i = t.identity
        assert (i.identity_id, i.identity_version) == ("mesc", 2)
        assert i.style_fingerprint == cf.STYLE_FP and i.format_profile == "talking_head"
        assert i.sound_identity == "mesc_sound"
        assert ("graphic_kind", "lower_third") in i.presets
        assert {z.name for z in i.zones} == {"caption_band", "lower_third"}
        assert [fnt.key for fnt in i.fonts] == ["display"]

    def test_the_build_names_the_specification_it_was_made_from(self) -> None:
        spec = f.compiled()

        t = f.built(f.facts_of())

        assert t.build.spec_hash == spec.compilation.spec_hash
        assert t.build.spec_asset_id == "spec-doc" and t.build.mode is BuildMode.NEW
        assert t.build.build_version == 1 and t.build.supersedes is None


class TestStatus:
    def test_a_clean_timeline_is_ok_and_says_what_it_has_not_checked(self) -> None:
        t = f.built()

        assert t.status is BuildStatus.PARTIAL or t.status is BuildStatus.OK
        assert t.validation.passed
        assert {k.value for k in t.validation.not_run} == {
            "black_frames",
            "missing_frames",
            "visual_gap",
            "blank_section",
            "missing_audio",
            "av_sync",
        }
        assert "unique_ids" in t.validation.checks_run

    def test_a_frame_exact_rational_is_never_a_float(self) -> None:
        t = f.built()

        b = video(t, "v:pB")
        assert isinstance(b.timeline_start, Rational)
        assert Fraction(b.timeline_start.numerator, b.timeline_start.denominator) == 4


class TestOverlappingFootage:
    def two_covers(self) -> BuiltTimeline:
        source = cf.source()
        # a second piece of cover footage over the first (5-6 s): 5.5-7 s, same layer
        second = cf.clip("pD", "broll1", 10, 12, 0, picture=True, layer=1)
        second = replace(
            second,
            timeline_start=Rational(11, 2),
            timeline_duration=Rational(3, 2),
            source_start=Rational(10, 1),
            source_end=Rational(23, 2),
            start_frame=round(5.5 * 25),
            duration_frames=round(1.5 * 25),
        )
        return f.built(f.facts_of(source=replace(source, clips=(*source.clips, second))))

    def test_overlapping_cover_footage_takes_its_own_lane_and_never_overlaps_a_track(self) -> None:
        t = self.two_covers()

        covers = [tr for tr in t.tracks if tr.role is Role.COVER]
        assert len(covers) == 2 and [tr.lane for tr in covers] == [1, 2]
        assert all(len(tr.video) == 1 for tr in covers)
        assert "lane 2" in covers[1].name and covers[1].group == covers[0].group
        assert "overlap_on_track" not in f.codes(t)

    def test_both_instances_play_the_same_source_with_their_own_ranges(self) -> None:
        t = self.two_covers()

        one, two = video(t, "v:pC"), video(t, "v:pD")
        assert one.source is not None and two.source is not None
        assert one.source.source_id == two.source.source_id == "broll1"
        assert one.source.source_in != two.source.source_in
        assert [s.source_id for s in t.sources].count("broll1") == 1
        assert {"v:pC", "v:pD"} <= set(t.source("broll1").used_by)  # type: ignore[union-attr]
