"""Transfer functions, gamut matrices, the grade and 3D LUTs."""

from pathlib import Path

import numpy as np
import pytest

from media_house.modules.video_improvement.domain.color import (
    OUTPUT_COLOR,
    ColorSpec,
    Primaries,
    Transfer,
    color_from_tags,
)
from media_house.modules.video_improvement.domain.errors import InvalidLut
from media_house.modules.video_improvement.domain.planning import ColorPlan
from media_house.modules.video_improvement.infrastructure import color_science as cs

REC709 = ColorSpec(Transfer.BT709, Primaries.BT709)
PROTUNE = ColorSpec(Transfer.GOPRO_PROTUNE, Primaries.BT709)
SLOG3 = ColorSpec(Transfer.SLOG3, Primaries.SGAMUT3_CINE)
GRID = np.linspace(0.0, 1.0, 101)


def plan(color: ColorSpec = REC709, **changes: object) -> ColorPlan:
    return ColorPlan(input_color=color, **changes)  # type: ignore[arg-type]


class TestVocabulary:
    def test_every_gradeable_name_has_an_implementation(self) -> None:
        for transfer in Transfer:
            if transfer in {Transfer.UNKNOWN, Transfer.HLG, Transfer.PQ}:
                assert transfer not in cs.TRANSFERS
            else:
                assert transfer in cs.TRANSFERS, transfer
        for primaries in Primaries:
            if primaries is Primaries.UNKNOWN:
                assert primaries not in cs.CHROMATICITIES
            else:
                assert primaries in cs.CHROMATICITIES, primaries

    def test_unknown_and_hdr_are_never_transformed(self) -> None:
        assert not cs.supported(ColorSpec())
        assert not cs.supported(ColorSpec(Transfer.PQ, Primaries.BT2020))
        with pytest.raises(ValueError, match="cannot be transformed"):
            cs.to_working(np.zeros(3), ColorSpec())

    def test_tags_map_to_colour_spaces_and_missing_ones_stay_unknown(self) -> None:
        assert color_from_tags("bt709", "bt709") == REC709
        assert color_from_tags("smpte2084", "bt2020") == ColorSpec(Transfer.PQ, Primaries.BT2020)
        assert color_from_tags("arib-std-b67", "bt2020").is_hdr
        assert color_from_tags(None, "bt709").transfer is Transfer.UNKNOWN
        assert not color_from_tags("something-else", "bt709").known
        assert not color_from_tags(None, None).known

    def test_flat_and_log_are_scene_referred_and_rec709_is_not(self) -> None:
        assert PROTUNE.scene_referred
        assert SLOG3.scene_referred
        assert not REC709.scene_referred


class TestTransferFunctions:
    @pytest.mark.parametrize("transfer", sorted(cs.TRANSFERS, key=str))
    def test_decoding_then_encoding_returns_the_signal(self, transfer: Transfer) -> None:
        function = cs.TRANSFERS[transfer]

        back = function.encode(function.decode(GRID))

        np.testing.assert_allclose(back, GRID, atol=1e-9)

    def test_protune_is_the_published_base_113_curve(self) -> None:
        function = cs.TRANSFERS[Transfer.GOPRO_PROTUNE]

        assert float(function.decode(np.array(0.0))) == pytest.approx(0.0)
        assert float(function.decode(np.array(1.0))) == pytest.approx(1.0)
        # y = log(112 x + 1) / log(113)  at x = 0.18
        assert float(function.encode(np.array(0.18))) == pytest.approx(0.6455, abs=1e-3)

    def test_slog3_puts_18_percent_grey_where_sony_documents_it(self) -> None:
        function = cs.TRANSFERS[Transfer.SLOG3]

        assert float(function.encode(np.array(0.18))) == pytest.approx(0.41056, abs=1e-4)
        assert float(function.decode(np.array(0.41056))) == pytest.approx(0.18, abs=1e-3)

    def test_vlog_puts_18_percent_grey_where_panasonic_documents_it(self) -> None:
        function = cs.TRANSFERS[Transfer.VLOG]

        assert float(function.encode(np.array(0.18))) == pytest.approx(0.42331, abs=1e-4)

    def test_rec709_video_is_decoded_with_the_display_gamma(self) -> None:
        function = cs.TRANSFERS[Transfer.BT709]

        assert float(function.decode(np.array(0.5))) == pytest.approx(0.5**2.4)

    def test_log_curves_keep_a_much_wider_range_than_the_display_white(self) -> None:
        peak = float(cs.TRANSFERS[Transfer.SLOG3].decode(np.array(1.0)))

        assert peak > 30  # S-Log3 full scale is far above display white


class TestGamut:
    def test_rec709_matrix_matches_the_published_one(self) -> None:
        matrix = cs.rgb_to_xyz_matrix(Primaries.BT709)

        expected = [[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]
        np.testing.assert_allclose(matrix, expected, atol=1e-3)

    @pytest.mark.parametrize("primaries", sorted(cs.CHROMATICITIES, key=str))
    def test_white_stays_white_in_every_gamut(self, primaries: Primaries) -> None:
        white = cs.gamut_matrix(primaries, Primaries.BT709) @ np.ones(3)

        np.testing.assert_allclose(white, np.ones(3), atol=1e-6)

    def test_same_primaries_are_the_identity(self) -> None:
        np.testing.assert_allclose(
            cs.gamut_matrix(Primaries.BT709, Primaries.BT709), np.eye(3), atol=1e-9
        )

    def test_going_to_a_gamut_and_back_is_lossless(self) -> None:
        there = cs.gamut_matrix(Primaries.BT709, Primaries.BT2020)
        back = cs.gamut_matrix(Primaries.BT2020, Primaries.BT709)

        np.testing.assert_allclose(back @ there, np.eye(3), atol=1e-9)

    def test_bt2020_to_rec709_matches_the_published_one(self) -> None:
        matrix = cs.gamut_matrix(Primaries.BT2020, Primaries.BT709)

        assert matrix[0, 0] == pytest.approx(1.6605, abs=2e-3)
        assert matrix[1, 1] == pytest.approx(1.1329, abs=2e-3)


class TestGrade:
    def test_a_plan_that_changes_nothing_returns_the_signal_exactly(self) -> None:
        rgb = np.random.default_rng(1).random((500, 3))

        out = cs.ColorTransform(plan())(rgb)

        np.testing.assert_allclose(out, rgb, atol=1e-9)
        assert plan().is_identity

    def test_one_stop_up_doubles_linear_light(self) -> None:
        mid = np.array([[0.4, 0.4, 0.4]])

        out = cs.ColorTransform(plan(exposure_stops=1.0))(mid)

        assert float(cs.to_working(out, REC709)[0, 0]) == pytest.approx(2 * 0.4**2.4, rel=1e-6)

    def test_gains_change_the_colour_balance_of_a_neutral(self) -> None:
        grey = np.array([[0.5, 0.5, 0.5]])

        out = cs.ColorTransform(plan(gains=(1.08, 1.0, 0.92)))(grey)[0]

        assert out[0] > out[1] > out[2]

    def test_saturation_zero_makes_grey_and_one_changes_nothing(self) -> None:
        colour = np.array([[0.8, 0.3, 0.2]])

        grey = cs.ColorTransform(plan(saturation=0.0))(colour)[0]

        assert np.ptp(grey) == pytest.approx(0.0, abs=1e-9)
        np.testing.assert_allclose(
            cs.ColorTransform(plan(saturation=1.0))(colour), colour, atol=1e-9
        )

    def test_vibrance_changes_vivid_colours_less_than_muted_ones(self) -> None:
        muted, vivid = np.array([[0.55, 0.5, 0.45]]), np.array([[0.9, 0.2, 0.1]])

        def change(color: np.ndarray, vibrance: float) -> float:
            out = cs.ColorTransform(plan(saturation=0.7, vibrance=vibrance))(color)
            return float(np.abs(out - color).max())

        assert change(vivid, 0.8) < change(vivid, 0.0)
        assert change(muted, 0.8) == pytest.approx(change(muted, 0.0), rel=0.35)

    def test_the_gamma_lift_raises_midtones_and_keeps_black_and_white(self) -> None:
        values = np.array([[0.0] * 3, [0.35] * 3, [1.0] * 3])

        out = cs.ColorTransform(plan(gamma=0.8))(values)

        assert out[0, 0] == pytest.approx(0.0, abs=1e-9)
        assert out[1, 0] > 0.35
        assert out[2, 0] == pytest.approx(1.0, abs=1e-9)

    def test_the_gamma_lift_softens_colour_in_the_shadows(self) -> None:
        dark_colour = np.array([[0.30, 0.12, 0.10]])

        lifted = cs.ColorTransform(plan(gamma=0.7))(dark_colour)[0]

        before = cs.ColorTransform(plan())(dark_colour)[0]
        assert lifted[0] / lifted[1] < before[0] / before[1]

    def test_the_rolloff_still_maps_the_lifted_white_to_one(self) -> None:
        out = cs.ColorTransform(plan(gamma=0.8, exposure_stops=0.5, knee=0.75))(np.ones((1, 3)))

        assert out[0, 0] == pytest.approx(1.0, abs=1e-6)

    def test_black_point_pulls_milky_blacks_down_and_keeps_white(self) -> None:
        out = cs.ColorTransform(plan(black_point=0.05))(np.array([[0.05] * 3, [1.0] * 3]))

        np.testing.assert_allclose(out, [[0.0] * 3, [1.0] * 3], atol=1e-9)

    def test_contrast_leaves_mid_grey_and_spreads_the_rest(self) -> None:
        grey = float(np.power(0.18, 1 / 2.4))
        values = np.array([[0.2] * 3, [grey] * 3, [0.8] * 3])

        out = cs.ColorTransform(plan(contrast=1.3))(values)

        assert out[1, 0] == pytest.approx(grey, abs=1e-6)
        assert out[0, 0] < 0.2
        assert out[2, 0] > 0.8

    def test_the_rolloff_is_smooth_monotonic_and_maps_white_to_one(self) -> None:
        x = np.linspace(0.0, 4.0, 400)

        y = cs.rolloff(x, 0.8, 4.0)

        assert np.all(np.diff(y) >= 0)
        np.testing.assert_allclose(y[x <= 0.8], x[x <= 0.8])
        assert y[-1] == pytest.approx(1.0)
        assert np.all(y <= 1.0 + 1e-12)

    def test_the_rolloff_has_no_kink_at_the_knee(self) -> None:
        x = np.array([0.79999, 0.8, 0.80001])

        y = cs.rolloff(x, 0.8, 3.0)

        slopes = np.diff(y) / np.diff(x)
        assert slopes[0] == pytest.approx(slopes[1], abs=0.05)

    def test_output_is_always_a_legal_signal(self) -> None:
        rgb = np.random.default_rng(5).random((2000, 3))
        hard = plan(
            exposure_stops=2.0, gains=(1.1, 1.0, 0.9), contrast=1.5, knee=0.7, saturation=1.5
        )

        out = cs.ColorTransform(hard)(rgb)

        assert out.min() >= 0.0
        assert out.max() <= 1.0

    def test_flat_footage_with_the_neutral_rendering_returns_the_original_picture(self) -> None:
        linear = np.power(np.random.default_rng(2).random((500, 3)) * 0.9 + 0.05, 2.4)
        flat = np.clip(cs.TRANSFERS[Transfer.GOPRO_PROTUNE].encode(linear), 0, 1)

        out = cs.ColorTransform(plan(PROTUNE))(flat)

        np.testing.assert_allclose(out, np.power(linear, 1 / 2.4), atol=1e-6)

    def test_log_footage_from_a_wide_gamut_camera_lands_inside_rec709(self) -> None:
        rgb = np.random.default_rng(4).random((3000, 3))

        out = cs.ColorTransform(plan(SLOG3, contrast=1.2, knee=0.75))(rgb)

        assert out.min() >= 0.0 and out.max() <= 1.0
        assert not np.isnan(out).any()

    def test_it_is_deterministic(self) -> None:
        rgb = np.random.default_rng(9).random((300, 3))
        p = plan(exposure_stops=0.7, gains=(1.05, 1, 0.95), contrast=1.1, saturation=1.1)

        np.testing.assert_array_equal(cs.ColorTransform(p)(rgb), cs.ColorTransform(p)(rgb))


class TestLuts:
    def test_the_baked_lut_approximates_the_transform(self) -> None:
        transform = cs.ColorTransform(
            plan(exposure_stops=0.5, gains=(1.05, 1, 0.95), contrast=1.1, knee=0.8, saturation=0.9)
        )
        lut = cs.CubeLut(cs.bake(transform, 65), "")
        rgb = np.random.default_rng(7).random((2000, 3))

        error = np.abs(cs.apply_lut(lut, rgb) - transform(rgb))

        assert error.max() < 0.01
        assert error.mean() < 0.002

    def test_the_identity_lut_changes_nothing_visible(self) -> None:
        lut = cs.CubeLut(cs.bake(cs.ColorTransform(plan()), 17), "")
        rgb = np.random.default_rng(8).random((500, 3))

        np.testing.assert_allclose(cs.apply_lut(lut, rgb), rgb, atol=1e-6)

    def test_a_written_cube_reads_back_with_red_varying_fastest(self, tmp_path: Path) -> None:
        table = cs.bake(cs.ColorTransform(plan(exposure_stops=0.4)), 9)
        path = tmp_path / "grade.cube"

        cs.write_cube(path, table, "test")
        lines = path.read_text(encoding="ascii").splitlines()
        back = cs.read_cube(path)

        assert lines[1] == "LUT_3D_SIZE 9"
        first, second = (list(map(float, line.split())) for line in lines[4:6])
        assert first == pytest.approx(table[0, 0, 0], abs=1e-6)
        assert second == pytest.approx(table[1, 0, 0], abs=1e-6)  # red moved first
        np.testing.assert_allclose(back.table, np.clip(table, 0, 1), atol=1e-6)
        assert len(back.sha256) == 64

    def test_the_hash_identifies_the_content(self, tmp_path: Path) -> None:
        table = cs.bake(cs.ColorTransform(plan()), 5)
        cs.write_cube(tmp_path / "a.cube", table, "a")
        cs.write_cube(tmp_path / "b.cube", table, "a")
        cs.write_cube(
            tmp_path / "c.cube", cs.bake(cs.ColorTransform(plan(exposure_stops=1)), 5), "a"
        )

        assert cs.read_cube(tmp_path / "a.cube").sha256 == cs.read_cube(tmp_path / "b.cube").sha256
        assert cs.read_cube(tmp_path / "a.cube").sha256 != cs.read_cube(tmp_path / "c.cube").sha256

    def test_a_look_lut_is_applied_last_and_blended_by_strength(self) -> None:
        invert = cs.CubeLut(1.0 - cs.bake(cs.ColorTransform(plan()), 17), "")
        grey = np.array([[0.3, 0.3, 0.3]])

        full = cs.ColorTransform(plan(look_lut="x", look_strength=1.0), invert)(grey)
        half = cs.ColorTransform(plan(look_lut="x", look_strength=0.5), invert)(grey)

        assert full[0, 0] == pytest.approx(0.7, abs=1e-6)
        assert half[0, 0] == pytest.approx(0.5, abs=1e-6)

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            ("LUT_1D_SIZE 4\n0 0 0\n", "only 3D"),
            ("LUT_3D_SIZE 2\n0 0 0\n", "expected 8 table rows"),
            ("0 0 0\n", "LUT_3D_SIZE"),
            ("LUT_3D_SIZE 2\nDOMAIN_MAX 2 2 2\n" + "0 0 0\n" * 8, "domain"),
            ("LUT_3D_SIZE two\n", "not a number"),
            ("LUT_3D_SIZE 2\n" + "a b c\n" * 8, "unexpected line"),
            ("LUT_3D_SIZE 2\n" + "0 0\n" * 8, "three finite numbers"),
            ("LUT_3D_SIZE 1\n0 0 0\n", "between 2 and 256"),
        ],
    )
    def test_unusable_cube_files_are_refused_with_a_reason(
        self, tmp_path: Path, content: str, message: str
    ) -> None:
        path = tmp_path / "bad.cube"
        path.write_text(content, encoding="ascii")

        with pytest.raises(InvalidLut, match=message):
            cs.read_cube(path)

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidLut):
            cs.read_cube(tmp_path / "missing.cube")

    def test_comments_titles_and_blank_lines_are_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.cube"
        rows = "\n".join(f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1))
        path.write_text(f'# a comment\nTITLE "x"\n\nLUT_3D_SIZE 2\n{rows}\n', encoding="ascii")

        assert cs.read_cube(path).size == 2

    def test_the_output_colour_is_rec709(self) -> None:
        assert OUTPUT_COLOR == REC709
