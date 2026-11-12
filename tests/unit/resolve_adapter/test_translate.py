"""From the built timeline to what Resolve should hold: every element traceable, every gap kept."""

from dataclasses import replace

import pytest

from media_house.core.domain import Rational
from media_house.modules.resolve_adapter.domain.capabilities import detect
from media_house.modules.resolve_adapter.domain.config import ResolveAdapterConfig
from media_house.modules.resolve_adapter.domain.errors import TimelineNotExecutable
from media_house.modules.resolve_adapter.domain.facts import CropFact, RetimeFact, TimelineFacts
from media_house.modules.resolve_adapter.domain.translate import (
    Expected,
    folder_for,
    frame_rate_text,
    translate,
)
from media_house.modules.resolve_adapter.domain.values import (
    CaptionStrategy,
    RetimePlacement,
    Strategy,
)
from tests.unit.resolve_adapter import fixtures as f


def go(facts: TimelineFacts | None = None, config: ResolveAdapterConfig | None = None) -> Expected:
    return translate(facts or f.facts_of(), config or ResolveAdapterConfig(), detect(None))


def gap_ops(expected: Expected) -> set[tuple[str, str]]:
    return {(g.element_id, g.operation) for g in expected.gaps}


class TestStructure:
    def test_tracks_are_numbered_from_the_bottom_in_the_builders_order(self) -> None:
        expected = go()

        video = [(t.track_id, t.index) for t in expected.tracks if t.kind == "video"]
        audio = [(t.track_id, t.index) for t in expected.tracks if t.kind == "audio"]
        assert video == [("V1", 1), ("V2", 2), ("V3", 3), ("V4", 4), ("V5", 5)]
        assert audio == [("A1", 1), ("A2", 2), ("A3", 3), ("A4", 4), ("A5", 5)]
        assert expected.track_count("video") == 5 and expected.track_count("audio") == 5

    def test_every_clip_becomes_one_item_with_the_builders_id(self) -> None:
        expected = go()

        assert {i.element_id for i in expected.items} == {
            "v:pA", "v:pB", "v:pC", "o:graphic:g1", "o:caption:cap:1", "o:caption:cap:2",
            "a:aV", "a:aW", "a:music:seg1", "a:sfx:sfx:1",
        }  # fmt: skip

    def test_position_length_and_the_part_of_the_file_it_plays(self) -> None:
        item = go().item("v:pB")

        assert item is not None
        assert (item.kind, item.track_index, item.start, item.duration) == ("video", 1, 100, 100)
        assert (item.src_start, item.src_frames) == (500, 100)
        assert item.asset_id == "rec1"

    def test_a_graphic_plays_its_part_of_the_shared_rendered_file(self) -> None:
        batch = go().item("o:caption:cap:2")

        assert batch is not None and batch.src_start == 50 and batch.track_index == 4

    def test_picture_and_sound_of_one_take_are_linked(self) -> None:
        expected = go()

        video, audio = expected.item("v:pA"), expected.item("a:aV")
        assert video is not None and audio is not None
        assert video.link == "a:aV" and audio.link == "v:pA"

    def test_project_settings_come_from_the_timeline(self) -> None:
        expected = go()

        assert dict(expected.settings) == {
            "timelineFrameRate": "25",
            "timelinePlaybackFrameRate": "25",
            "timelineResolutionWidth": "1920",
            "timelineResolutionHeight": "1080",
        }

    def test_the_translation_is_deterministic(self) -> None:
        assert go() == go()

    def test_comparable_fields_are_sorted_and_complete(self) -> None:
        item = go().item("v:pB")

        assert item is not None
        names = [k for k, _ in item.fields]
        assert names == sorted(names)
        assert {"asset", "duration", "enabled", "src_start", "start", "track"} <= set(names)


class TestWhatScriptingCannotDo:
    def test_a_transition_is_kept_as_an_instruction_at_the_cut(self) -> None:
        expected = go()

        gap = next(g for g in expected.gaps if g.operation == "transition.insert")
        assert gap.element_id == "cut:pA>pB" and gap.strategy is Strategy.MANUAL_MARKER
        assert "dissolve" in gap.detail and "4 frames" in gap.detail and gap.alternatives
        marker = next(
            m for m in expected.markers if m.custom.endswith("cut:pA>pB:transition.insert")
        )
        assert (
            marker.frame == 100
            and marker.color == "Yellow"
            and "TRANSITION dissolve" in marker.name
        )

    def test_without_instruction_markers_the_gap_is_reported_but_nothing_is_drawn(self) -> None:
        expected = go(config=ResolveAdapterConfig(instruction_markers=False))

        assert any(g.operation == "transition.insert" for g in expected.gaps)
        assert not any("gap" in m.custom for m in expected.markers)

    def test_clip_gain_and_fades_are_left_to_the_mix_engine(self) -> None:
        expected = go()

        gap = next(g for g in expected.gaps if g.element_id == "a:music:seg1")
        assert gap.operation == "audio.gain_fades" and gap.owner == "audio_mix_engine"
        assert gap.strategy is Strategy.NOT_APPLIED

    def test_a_retime_the_application_must_perform_is_placed_at_real_speed_with_a_marker(
        self,
    ) -> None:
        facts = f.with_clip(
            f.facts_of(), "v:pA", retime=RetimeFact("constant", True, "2/1x"), source_frames=200
        )

        expected = go(facts)

        item = expected.item("v:pA")
        assert item is not None and item.duration == 100 and item.src_frames == 100  # real speed
        assert ("v:pA", "clip.retime") in gap_ops(expected)
        assert any(
            m.custom.endswith("v:pA:clip.retime") and m.color == "Red" for m in expected.markers
        )

    def test_or_the_clip_can_be_left_out_and_reported(self) -> None:
        facts = f.with_clip(f.facts_of(), "v:pA", retime=RetimeFact("ramp", True, ""))

        expected = go(facts, ResolveAdapterConfig(retime_placement=RetimePlacement.SKIP))

        assert expected.item("v:pA") is None
        gap = next(g for g in expected.gaps if g.element_id == "v:pA")
        assert gap.placed is False and gap.strategy is Strategy.NOT_APPLIED

    def test_a_retime_already_baked_into_the_file_needs_nothing(self) -> None:
        facts = f.with_clip(f.facts_of(), "v:pA", retime=RetimeFact("ramp", False, ""))

        assert ("v:pA", "clip.retime") not in gap_ops(go(facts))

    def test_a_native_effect_is_an_instruction_not_a_substitute(self) -> None:
        facts = f.with_clip(f.facts_of(), "v:pA", native_effects=("vignette",))

        expected = go(facts)

        gap = next(g for g in expected.gaps if g.operation == "effect.native")
        assert "vignette" in gap.detail and gap.element_id == "v:pA"

    def test_a_moving_crop_cannot_be_keyframed(self) -> None:
        facts = f.with_clip(f.facts_of(), "v:pA", crop=CropFact(0.1, 0, 0.3, 1, True, 3))

        expected = go(facts)

        assert ("v:pA", "clip.crop_keyframed") in gap_ops(expected)
        item = expected.item("v:pA")
        assert item is not None and not any(k == "ZoomX" for k, _ in item.props)

    def test_a_clip_whose_file_is_gone_is_reported_not_placed(self) -> None:
        facts = f.with_clip(f.facts_of(), "v:pC", resolved=False)

        expected = go(facts)

        assert expected.item("v:pC") is None
        gap = next(g for g in expected.gaps if g.element_id == "v:pC")
        assert gap.operation == "clip.place" and gap.placed is False

    def test_a_colour_profile_the_project_would_need_is_reported_not_forced(self) -> None:
        facts = f.facts_of()
        facts = replace(facts, settings=replace(facts.settings, color_profile="rec2020_hlg"))

        assert ("timeline", "project.settings") in gap_ops(go(facts))


class TestPictureGeometry:
    def portrait_source(self, fit: str) -> TimelineFacts:
        facts = f.facts_of()
        media = tuple(
            replace(m, width=1080, height=1920) if m.asset_id == "rec1" else m for m in facts.media
        )
        facts = replace(facts, media=media)
        return f.with_clip(f.with_clip(facts, "v:pA", fit=fit), "v:pB", fit=fit)

    def test_a_source_that_fits_needs_no_geometry(self) -> None:
        item = go().item("v:pA")

        assert item is not None and item.props == ()

    def test_contain_asks_for_nothing_because_resolve_fits_by_default(self) -> None:
        item = go(self.portrait_source("contain")).item("v:pA")

        assert item is not None and item.props == ()

    def test_cover_zooms_the_fitted_picture_until_the_frame_is_full(self) -> None:
        item = go(self.portrait_source("cover")).item("v:pA")

        assert item is not None
        zoom = dict(item.props)["ZoomX"]
        assert zoom == pytest.approx((1920 / 1080) / (1080 / 1920), rel=1e-6)
        assert dict(item.props)["ZoomY"] == zoom

    def test_stretch_scales_each_axis_alone(self) -> None:
        item = go(self.portrait_source("stretch")).item("v:pA")

        assert item is not None
        props = dict(item.props)
        assert props["ZoomX"] != props["ZoomY"]

    def test_a_static_reframing_window_becomes_zoom_and_pan(self) -> None:
        facts = f.facts_of()
        facts = replace(facts, settings=replace(facts.settings, width=1080, height=1920))
        facts = f.with_clip(facts, "v:pA", crop=CropFact(0.2, 0.0, 0.3164, 1.0, False, 1))

        item = go(facts).item("v:pA")

        assert item is not None
        props = dict(item.props)
        assert props["ZoomX"] == pytest.approx(props["ZoomY"])
        assert props["ZoomX"] > 3
        assert props["Pan"] > 0  # the window is left of centre: the picture moves right
        assert "Tilt" not in props  # the window spans the full height

    def test_a_moving_window_into_another_aspect_is_held_at_its_first_key(self) -> None:
        # E2E regression: a 9:16 Short of 16:9 footage rendered letterboxed (Fusion zoom of 1.0)
        facts = f.facts_of()
        facts = replace(facts, settings=replace(facts.settings, width=1080, height=1920))
        facts = f.with_clip(facts, "v:pA", crop=CropFact(0.2, 0.0, 0.3164, 1.0, True, 3))

        expected = go(facts)

        item = expected.item("v:pA")
        assert item is not None and dict(item.props)["ZoomX"] > 3
        assert ("v:pA", "clip.crop_keyframed") not in gap_ops(expected)

    def test_opacity_is_a_percentage(self) -> None:
        item = go(f.with_clip(f.facts_of(), "o:graphic:g1", opacity=0.5)).item("o:graphic:g1")

        assert item is not None and dict(item.props)["Opacity"] == 50.0


class TestOtherInputs:
    def test_a_frame_rate_resolve_has_no_name_for_is_refused_before_anything_is_touched(
        self,
    ) -> None:
        facts = f.facts_of()
        facts = replace(facts, settings=replace(facts.settings, frame_rate=Rational(7, 2)))

        with pytest.raises(TimelineNotExecutable, match="frame rate"):
            go(facts)

    @pytest.mark.parametrize(
        ("rate", "text"),
        [
            (Rational(30000, 1001), "29.97"),
            (Rational(24000, 1001), "23.976"),
            (Rational(60000, 1001), "59.94"),
            (Rational(25, 1), "25"),
            (Rational(50, 1), "50"),
        ],
    )
    def test_frame_rates_are_named_the_way_resolve_names_them(
        self, rate: Rational, text: str
    ) -> None:
        assert frame_rate_text(rate) == text

    def test_editable_caption_cues_are_only_offered_when_asked_for(self) -> None:
        facts = f.facts_of()

        assert go(facts).subtitles is None
        offered = go(
            facts,
            ResolveAdapterConfig(caption_strategy=CaptionStrategy.RENDERED_WITH_EDITABLE_CUES),
        )
        assert offered.subtitles is not None and offered.subtitles.cues

    def test_section_markers_are_carried_over_with_a_tag(self) -> None:
        expected = go()

        marker = next(m for m in expected.markers if m.custom.startswith("mh:marker:"))
        assert marker.color == "Blue"

    @pytest.mark.parametrize(
        ("relation", "media_type", "roles", "folder"),
        [
            ("original", "video", ("primary",), "Original footage"),
            ("original", "audio", (), "Original audio"),
            ("improved_video", "video", (), "Processed media"),
            ("rendered", "video", ("graphics",), "Graphics"),
            ("rendered", "video", ("captions",), "Captions"),
            ("library", "audio", ("music",), "Music"),
            ("library", "audio", ("sfx",), "Sound effects"),
            ("rendered", "video", ("effects",), "Generated"),
        ],
    )
    def test_media_is_organised_by_what_it_is_and_what_plays_it(
        self, relation: str, media_type: str, roles: tuple[str, ...], folder: str
    ) -> None:
        media = replace(
            f.facts_of().media[0], relation=relation, media_type=media_type, roles=roles
        )

        assert folder_for(media, "Media House") == ("Media House", folder)
