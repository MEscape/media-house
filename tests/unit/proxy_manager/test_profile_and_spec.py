"""Profiles are requests; the resolved spec is the deterministic, machine-independent answer."""

import json
from fractions import Fraction

import pytest

from media_house.modules.proxy_manager.domain.errors import InvalidProfile, UnsupportedSource
from media_house.modules.proxy_manager.domain.profile import PROFILES, resolve_profile
from media_house.modules.proxy_manager.domain.source import SourceFacts
from media_house.modules.proxy_manager.domain.spec import (
    START_REBASED,
    ResolvedSpec,
    reservation_bytes,
    resolve_spec,
)
from media_house.modules.proxy_manager.domain.values import AudioPolicy, Codec, JsonValue
from tests.support.proxy_fakes import audio_facts, source_facts, video_facts


def spec_for(profile: str, facts: SourceFacts, **overrides: JsonValue) -> ResolvedSpec:
    return resolve_spec(resolve_profile(profile, overrides), facts)


def test_built_in_profiles_exist_and_name_themselves() -> None:
    assert {"analysis", "edit"} <= set(PROFILES)
    assert all(p.name == name for name, p in PROFILES.items())


def test_overrides_change_only_the_named_fields() -> None:
    base = PROFILES["analysis"]
    changed = resolve_profile("analysis", {"max_long_edge": 480})

    assert changed.max_long_edge == 480
    assert changed.crf == base.crf and changed.codec is base.codec and changed.version == 1


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        ("nope", {}),
        ("analysis", {"codec": "prores"}),
        ("analysis", {"max_long_edge": 8}),
        ("analysis", {"max_long_edge": "big"}),
        ("analysis", {"crf": 99}),
        ("analysis", {"full_decode_validation": "yes"}),
        ("analysis", {"audio": "some"}),
        ("analysis", {"max_long_edge": True}),
    ],
)
def test_contradictory_or_unknown_profiles_are_refused_before_any_work(
    name: str, overrides: dict[str, object]
) -> None:
    with pytest.raises(InvalidProfile):
        resolve_profile(name, overrides)  # type: ignore[arg-type]


def test_a_landscape_source_is_scaled_to_the_long_edge_keeping_its_shape() -> None:
    spec = spec_for("analysis", source_facts())

    assert (spec.width, spec.height) == (640, 360)
    assert spec.pixel_format == "yuv420p" and spec.container == "mp4"


def test_a_rotated_source_keeps_its_coded_orientation_and_its_rotation() -> None:
    facts = source_facts(video_facts(width=1920, height=1080, rotation=90))

    spec = spec_for("analysis", facts)

    assert (spec.width, spec.height) == (640, 360)  # coded orientation; players still rotate it
    assert spec.rotation == 90


def test_the_pixel_shape_is_baked_into_square_pixels() -> None:
    facts = source_facts(video_facts(width=720, height=576, sar=Fraction(16, 15)))

    spec = spec_for("analysis", facts)

    assert (spec.width, spec.height) == (640, 480)  # 4:3 as displayed


def test_a_small_source_is_never_upscaled() -> None:
    spec = spec_for("analysis", source_facts(video_facts(width=320, height=240)))

    assert (spec.width, spec.height) == (320, 240)


def test_dimensions_are_always_even() -> None:
    spec = spec_for("analysis", source_facts(video_facts(width=1281, height=721)))

    assert spec.width % 2 == 0 and spec.height % 2 == 0


def test_audio_policy_selects_the_streams_and_the_codec_follows_the_profile() -> None:
    two = source_facts(audio=[audio_facts(index=1), audio_facts(index=2, channels=1)])

    first = spec_for("analysis", two)
    everything = spec_for("edit", two)

    assert [a.source_index for a in first.audio] == [1] and first.audio[0].codec == "aac"
    assert [a.source_index for a in everything.audio] == [1, 2]
    assert everything.audio[0].codec == "pcm_s16le" and everything.codec is Codec.PRORES
    assert everything.audio[1].channels == 1


def test_a_source_without_audio_maps_no_audio() -> None:
    assert spec_for("analysis", source_facts(audio=[])).audio == ()


def test_audio_only_media_is_unsupported_with_a_reason() -> None:
    facts = SourceFacts(None, (audio_facts(),), None, "probe")

    with pytest.raises(UnsupportedSource, match="no video"):
        resolve_spec(PROFILES["analysis"], facts)


def test_a_source_that_does_not_start_at_zero_is_recorded_as_a_rebased_start() -> None:
    late = source_facts(video_facts(first_pts=1.5), [audio_facts(first=1.5)])

    assert spec_for("analysis", late).transformations == (START_REBASED,)
    assert spec_for("analysis", source_facts()).transformations == ()


def test_the_config_is_deterministic_json_without_machine_details() -> None:
    facts = source_facts()

    one, two = spec_for("analysis", facts).to_config(), spec_for("analysis", facts).to_config()

    assert one == two
    text = json.dumps(one, sort_keys=True)
    assert "libx264" not in text and "ffmpeg" not in text


def test_every_output_affecting_setting_changes_the_config() -> None:
    facts = source_facts(audio=[audio_facts(index=1), audio_facts(index=2)])
    base = spec_for("analysis", facts).to_config()

    for override in (
        {"max_long_edge": 480},
        {"crf": 30},
        {"video_maxrate_kbps": 900},
        {"audio_bitrate_kbps": 64},
        {"audio": "all"},
    ):
        assert spec_for("analysis", facts, **override).to_config() != base, override


def test_validation_policy_does_not_change_what_is_generated() -> None:
    facts = source_facts()

    plain = spec_for("analysis", facts).to_config()
    decoded = spec_for("analysis", facts, full_decode_validation=True).to_config()

    assert plain == decoded  # checking harder is not a different proxy


def test_reservation_grows_with_duration_and_never_below_the_overhead() -> None:
    short = source_facts(video_facts(seconds=2))
    long = source_facts(video_facts(seconds=600))

    for profile in ("analysis", "edit"):
        small, big = (reservation_bytes(spec_for(profile, f), f) for f in (short, long))
        assert 0 < small < big


def test_h264_reservation_is_above_the_rate_ceiling() -> None:
    facts = source_facts(video_facts(seconds=100))
    spec = spec_for("analysis", facts)

    ceiling = (1500 + 96) * 1000 / 8 * 100

    assert reservation_bytes(spec, facts) > ceiling * 1.13  # the measured worst case over it


def test_audio_policy_enum_values_are_the_override_spelling() -> None:
    assert {p.value for p in AudioPolicy} == {"first", "all"}
