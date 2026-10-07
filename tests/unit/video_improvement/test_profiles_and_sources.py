"""Which profile applies, how configuration is overridden, and what makes a result cacheable."""

import json
from dataclasses import replace

import pytest

from media_house.modules.video_improvement.domain.color import ColorSpec, Primaries, Transfer
from media_house.modules.video_improvement.domain.errors import InvalidProfile
from media_house.modules.video_improvement.domain.profiles import (
    apply_overrides,
    processing_profile_names,
    resolve_processing_profile,
)
from media_house.modules.video_improvement.domain.source import (
    SourceProfile,
    get_source_profile,
    resolve_source_profile,
    source_profile_names,
)
from media_house.modules.video_improvement.domain.values import FactsSource, ProfileOrigin
from media_house.modules.video_improvement.infrastructure import color_science as cs
from tests.support.video_fakes import REC709, facts

GOPRO_HERO9 = {"firmware": "HD9.01.01.60.00"}


class TestSourceProfileResolution:
    def test_the_caller_always_wins_over_what_the_file_says(self) -> None:
        clip = facts(tags=GOPRO_HERO9, color=REC709)

        resolved = resolve_source_profile("studio_camera", clip)

        assert resolved.profile.name == "studio_camera"
        assert resolved.origin is ProfileOrigin.EXPLICIT

    def test_gopro_firmware_identifies_the_hero9(self) -> None:
        resolved = resolve_source_profile(
            None, facts(tags=GOPRO_HERO9, source=FactsSource.INSPECTION)
        )

        assert resolved.profile.name == "gopro_hero_9"
        assert resolved.origin is ProfileOrigin.INSPECTION
        assert "HD9.01.01.60.00" in resolved.evidence
        assert resolved.profile.default_processing == "outdoor"

    def test_the_same_metadata_read_from_the_file_has_a_different_origin(self) -> None:
        resolved = resolve_source_profile(None, facts(tags=GOPRO_HERO9, source=FactsSource.PROBE))

        assert resolved.profile.name == "gopro_hero_9"
        assert resolved.origin is ProfileOrigin.EMBEDDED_METADATA

    def test_flat_footage_is_never_guessed_from_a_gopro_file(self) -> None:
        assert resolve_source_profile(None, facts(tags=GOPRO_HERO9)).profile.name != "gopro_flat"
        assert get_source_profile("gopro_flat").match is None

    def test_declared_rec709_tags_select_the_rec709_profile(self) -> None:
        resolved = resolve_source_profile(None, facts(source=FactsSource.INSPECTION))

        assert resolved.profile.name == "rec709"
        assert resolved.origin is ProfileOrigin.INSPECTION

    def test_untagged_hd_video_is_read_as_rec709_by_convention(self) -> None:
        resolved = resolve_source_profile(None, facts(color=ColorSpec()))

        assert resolved.profile.name == "rec709"
        assert resolved.origin is ProfileOrigin.DETECTED

    def test_untagged_sd_video_stays_generic_and_unknown(self) -> None:
        resolved = resolve_source_profile(None, facts(color=ColorSpec(), width=720, height=480))

        assert resolved.profile.name == "generic"
        assert resolved.origin is ProfileOrigin.DEFAULT
        assert not resolved.profile.input_color.known

    def test_a_partly_tagged_file_is_not_called_rec709(self) -> None:
        clip = facts(color=ColorSpec(Transfer.BT709, Primaries.UNKNOWN))

        assert resolve_source_profile(None, clip).profile.name == "generic"

    def test_an_unknown_profile_name_is_refused_with_the_choices(self) -> None:
        with pytest.raises(InvalidProfile, match="gopro_hero_9"):
            resolve_source_profile("nikon", facts())

    def test_every_known_input_colour_can_actually_be_transformed(self) -> None:
        for name in source_profile_names():
            color = get_source_profile(name).input_color
            assert not color.known or cs.supported(color), name

    def test_the_generic_profile_is_the_only_one_without_a_colour_space(self) -> None:
        unknown = [n for n in source_profile_names() if not get_source_profile(n).input_color.known]

        assert unknown == ["generic"]

    def test_log_cameras_differ_from_hero9_only_in_data(self) -> None:
        for name in ("gopro_flat", "sony_slog3", "panasonic_vlog"):
            assert get_source_profile(name).input_color.scene_referred


class TestProcessingProfiles:
    def test_profiles_exist_and_are_valid(self) -> None:
        for name in processing_profile_names():
            assert resolve_processing_profile(name).name == name

    def test_unknown_profile_is_refused(self) -> None:
        with pytest.raises(InvalidProfile, match="natural"):
            resolve_processing_profile("cinematic-orange")

    def test_studio_is_stricter_than_outdoor_about_noise_and_colour(self) -> None:
        studio, outdoor = (
            resolve_processing_profile("studio"),
            resolve_processing_profile("outdoor"),
        )

        assert (
            studio.denoise.trigger_sigma > outdoor.denoise.trigger_sigma
        )  # tolerates less processing
        assert studio.color.white_balance_strength > outdoor.color.white_balance_strength
        assert outdoor.color.highlight_knee < studio.color.highlight_knee  # earlier roll-off
        assert outdoor.color.max_exposure_stops > studio.color.max_exposure_stops

    def test_the_default_profile_does_the_least(self) -> None:
        natural = resolve_processing_profile("natural")

        assert natural.color.contrast == 1.0
        assert not natural.look.lut_path


class TestOverrides:
    source = get_source_profile("rec709")
    base = resolve_processing_profile("natural")

    def test_a_dotted_path_changes_exactly_one_setting(self) -> None:
        config = apply_overrides(self.source, self.base, {"color.saturation_max": 0.38})

        assert config.processing.color.saturation_max == 0.38
        assert replace(config.processing.color, saturation_max=0.5) == self.base.color

    def test_source_settings_use_the_source_prefix(self) -> None:
        config = apply_overrides(self.source, self.base, {"source.input_color.transfer": "slog3"})

        assert config.source.input_color.transfer is Transfer.SLOG3
        assert config.source.name == "rec709"  # the rest of the profile is unchanged

    def test_the_processing_prefix_is_optional(self) -> None:
        a = apply_overrides(self.source, self.base, {"processing.denoise.enabled": False})
        b = apply_overrides(self.source, self.base, {"denoise.enabled": False})

        assert a == b

    def test_an_integer_is_accepted_for_a_number_setting(self) -> None:
        assert (
            apply_overrides(self.source, self.base, {"color.contrast": 1}).processing.color.contrast
            == 1.0
        )

    @pytest.mark.parametrize(
        ("path", "value", "message"),
        [
            ("color.nonsense", 1, "unknown setting"),
            ("nonsense.value", 1, "unknown setting"),
            ("color", 1, "group of settings"),
            ("color.saturation_max", "high", "expected float"),
            ("color.enabled", 1, "expected bool"),
            ("color.saturation_max", 7.0, "saturation band"),
            ("denoise.min_strength", 2.0, "denoise strengths"),
            ("name", "x", "cannot be overridden"),
            ("source.version", 9, "cannot be overridden"),
            ("source.input_color.transfer", "sepia", "not a valid choice"),
            ("output.codec", "gif", "codec must be"),
            ("execution.hardware", "tpu", "hardware must be"),
            ("source", 1, "incomplete setting"),
        ],
    )
    def test_bad_overrides_are_refused_with_the_reason(
        self, path: str, value: object, message: str
    ) -> None:
        with pytest.raises(InvalidProfile, match=message):
            apply_overrides(self.source, self.base, {path: value})  # type: ignore[dict-item]

    def test_overrides_never_mutate_the_built_in_profiles(self) -> None:
        apply_overrides(self.source, self.base, {"color.contrast": 1.4})

        assert resolve_processing_profile("natural").color.contrast == 1.0

    def test_order_of_overrides_does_not_matter(self) -> None:
        one = apply_overrides(
            self.source, self.base, {"color.contrast": 1.1, "denoise.enabled": False}
        )
        two = apply_overrides(
            self.source, self.base, {"denoise.enabled": False, "color.contrast": 1.1}
        )

        assert one == two


class TestIdentity:
    """What decides the result must change the fingerprint; what only decides speed must not."""

    base = resolve_processing_profile("natural")

    def config(self, **overrides: object) -> str:
        profile = apply_overrides(get_source_profile("rec709"), self.base, overrides).processing  # type: ignore[arg-type]
        return json.dumps(profile.to_config(), sort_keys=True)

    @pytest.mark.parametrize(
        ("path", "value"),
        [
            ("color.target_median", 0.2),
            ("color.contrast", 1.1),
            ("color.saturation_max", 0.4),
            ("color.vibrance", 0.2),
            ("denoise.enabled", False),
            ("denoise.max_strength", 0.5),
            ("sharpen.max_amount", 0.4),
            ("look.strength", 0.5),
            ("look.lut_path", "look.cube"),
            ("output.crf", 18),
            ("output.codec", "h265"),
            ("output.bit_depth", 10),
            ("execution.lut_size", 33),
            ("execution.sample_count", 12),
        ],
    )
    def test_every_setting_that_changes_the_result_changes_the_identity(
        self, path: str, value: object
    ) -> None:
        assert self.config(**{path: value}) != self.config()

    def test_the_hardware_choice_is_not_part_of_the_identity(self) -> None:
        assert self.config(**{"execution.hardware": "cpu"}) == self.config()

    def test_the_source_profile_identity_includes_its_version_and_colour(self) -> None:
        flat = get_source_profile("gopro_flat").to_config()
        newer = replace(get_source_profile("gopro_flat"), version=2).to_config()

        assert flat != newer
        assert flat["input_transfer"] == "gopro_protune"
        assert flat["rendering_contrast"] == get_source_profile("gopro_flat").rendering.contrast

    def test_a_source_profile_value_is_all_data(self) -> None:
        profile = SourceProfile(
            "custom_cam", "x", input_color=ColorSpec(Transfer.SLOG3, Primaries.SGAMUT3_CINE)
        )

        assert profile.to_config()["input_transfer"] == "slog3"
