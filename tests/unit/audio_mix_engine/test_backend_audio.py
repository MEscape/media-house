"""The real FFmpeg backend on real audio: what comes out is measured, not assumed."""

import shutil
from pathlib import Path

import pytest

from media_house.modules.audio_mix_engine.domain.errors import BackendFailed
from media_house.modules.audio_mix_engine.domain.profile import Compressor, EqBand
from media_house.modules.audio_mix_engine.domain.spec import Automation, Duck, GainPoint
from media_house.modules.audio_mix_engine.domain.validate import ALL_OPERATIONS
from media_house.modules.audio_mix_engine.domain.values import BusRole, Interpolation, Provenance
from media_house.modules.audio_mix_engine.infrastructure.ffmpeg_backend import BACKEND_REVISION
from tests.support.media_factories import ffmpeg
from tests.unit.audio_mix_engine import audio_support as a

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg")

be = a.backend()


def lufs(path: Path, start: float, end: float) -> float:
    """Integrated loudness of a part; below the measurement gate counts as -70."""
    value = be.measure_range(path, start, end, a.CANCEL)
    return -70.0 if value is None else value


@pytest.fixture
def voice(tmp_path: Path) -> Path:
    return a.tone(tmp_path / "voice.wav", 6.0)


@pytest.fixture
def music(tmp_path: Path) -> Path:
    return a.noise(tmp_path / "music.wav", 8.0)


def test_the_backend_can_do_everything_the_engine_may_ask_of_it() -> None:
    caps = be.capabilities()

    assert caps.identity.startswith("ffmpeg ") and caps.identity.endswith(f" r{BACKEND_REVISION}")
    assert caps.operations >= ALL_OPERATIONS


class TestPlacementAndLength:
    def test_a_stem_is_exactly_the_length_of_the_timeline_whatever_the_clips_do(
        self, tmp_path: Path, voice: Path
    ) -> None:
        path = a.stem(tmp_path, (a.clip("c", "v", 1.0, 2.0),), {"v": voice}, seconds=5.0)

        m = be.measure(path, a.CANCEL)

        assert (m.duration_samples, m.sample_rate, m.channels, m.codec) == (
            240000,
            48000,
            2,
            "pcm_f32le",
        )

    def test_a_clip_sits_where_the_timeline_puts_it_to_the_sample(
        self, tmp_path: Path, voice: Path
    ) -> None:
        path = a.stem(tmp_path, (a.clip("c", "v", 1.0, 2.0),), {"v": voice}, seconds=5.0)

        silent = be.silences(path, -60.0, 0.5, a.CANCEL)

        assert len(silent) == 2
        (s0, e0), (s1, e1) = silent
        assert s0 == pytest.approx(0.0, abs=0.001) and e0 == pytest.approx(1.0, abs=0.002)
        assert s1 == pytest.approx(3.0, abs=0.002) and e1 == pytest.approx(5.0, abs=0.002)

    def test_a_source_at_another_rate_is_cut_where_asked_and_resampled(
        self, tmp_path: Path
    ) -> None:
        src = a.tone(tmp_path / "s44.wav", 4.0, rate=44100)
        path = a.stem(
            tmp_path,
            (a.clip("c", "s", 0.0, 1.0, src_in=1.0),),
            {"s": src},
            seconds=2.0,
            rates={"s": 44100},
        )

        m = be.measure(path, a.CANCEL)

        assert m.sample_rate == 48000 and m.duration_samples == 96000
        assert be.silences(path, -60.0, 0.5, a.CANCEL)[0][0] == pytest.approx(1.0, abs=0.003)

    def test_a_source_whose_rate_is_unknown_is_cut_by_time(
        self, tmp_path: Path, voice: Path
    ) -> None:
        path = a.stem(
            tmp_path,
            (a.clip("c", "v", 0.0, 1.0, src_in=2.0),),
            {"v": voice},
            seconds=2.0,
            rates={"v": None},
        )

        assert be.silences(path, -60.0, 0.5, a.CANCEL)[0][0] == pytest.approx(1.0, abs=0.01)

    def test_a_clip_that_plays_faster_takes_less_timeline(
        self, tmp_path: Path, voice: Path
    ) -> None:
        path = a.stem(tmp_path, (a.clip("c", "v", 0.0, 1.0, speed=2.0),), {"v": voice}, seconds=3.0)

        assert be.silences(path, -60.0, 0.5, a.CANCEL)[0][0] == pytest.approx(1.0, abs=0.01)

    def test_the_same_inputs_render_the_same_samples(self, tmp_path: Path, voice: Path) -> None:
        one = a.stem(
            tmp_path,
            (a.clip("c", "v", 0.5, 2.0, gain=-3.0, fade_in=0.2),),
            {"v": voice},
            seconds=4.0,
            name="1.wav",
        )
        two = a.stem(
            tmp_path,
            (a.clip("c", "v", 0.5, 2.0, gain=-3.0, fade_in=0.2),),
            {"v": voice},
            seconds=4.0,
            name="2.wav",
        )

        assert be.null_test(one, two, a.CANCEL) == -200.0


class TestLevelsAndFades:
    def test_clip_gain_moves_the_level_by_that_much(self, tmp_path: Path, voice: Path) -> None:
        loud = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0),), {"v": voice}, seconds=4.0, name="0.wav"
        )
        soft = a.stem(
            tmp_path,
            (a.clip("c", "v", 0.0, 4.0, gain=-6.0),),
            {"v": voice},
            seconds=4.0,
            name="6.wav",
        )

        assert lufs(loud, 0.5, 3.5) - lufs(soft, 0.5, 3.5) == pytest.approx(6.0, abs=0.3)

    def test_a_fade_out_takes_the_tail_down(self, tmp_path: Path, voice: Path) -> None:
        path = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0, fade_out=2.0),), {"v": voice}, seconds=4.0
        )

        assert lufs(path, 0.5, 1.5) - lufs(path, 3.5, 4.0) > 10

    def test_a_fade_in_brings_the_start_up(self, tmp_path: Path, voice: Path) -> None:
        path = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0, fade_in=2.0),), {"v": voice}, seconds=4.0
        )

        assert lufs(path, 2.5, 3.5) - lufs(path, 0.0, 0.5) > 10

    def test_an_equal_power_crossfade_keeps_the_level_across_the_cut(self, tmp_path: Path) -> None:
        one = a.tone(tmp_path / "one.wav", 4.0, hz=440)
        two = a.tone(tmp_path / "two.wav", 4.0, hz=440)
        clips = (
            a.clip("a", "one", 0.0, 2.1, fade_out=0.2, curve="equal_power"),
            a.clip("b", "two", 1.9, 2.1, fade_in=0.2, curve="equal_power"),
        )

        path = a.stem(tmp_path, clips, {"one": one, "two": two}, seconds=4.0)

        # coherent material: equal-power sums to about +3 dB at the crossing, never a dip
        assert lufs(path, 1.8, 2.3) >= lufs(path, 0.5, 1.5) - 1.0

    def test_automation_draws_the_gain_over_time(self, tmp_path: Path, voice: Path) -> None:
        auto = Automation(
            "auto", "bus", "dialogue",
            (GainPoint(0, 0.0), GainPoint(96000, 0.0), GainPoint(144000, -20.0)),
            Interpolation.LINEAR, Provenance.HUMAN, "x",
        )  # fmt: skip
        path = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 5.0),), {"v": voice}, seconds=5.0, automation=(auto,)
        )

        assert lufs(path, 0.2, 1.5) - lufs(path, 3.5, 4.8) == pytest.approx(20.0, abs=1.5)

    def test_smooth_automation_reaches_the_same_levels(self, tmp_path: Path, voice: Path) -> None:
        auto = Automation(
            "auto", "bus", "dialogue", (GainPoint(48000, 0.0), GainPoint(96000, -12.0)),
            Interpolation.SMOOTH, Provenance.HUMAN, "x",
        )  # fmt: skip
        path = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0),), {"v": voice}, seconds=4.0, automation=(auto,)
        )

        assert lufs(path, 0.2, 0.9) - lufs(path, 2.5, 3.8) == pytest.approx(12.0, abs=1.0)


class TestDucking:
    def test_music_goes_down_under_speech_and_comes_back(self, tmp_path: Path, music: Path) -> None:
        duck = Duck("d", ("m",), "speech", 96000, 192000, 12.0, 0.2, 0.4, Provenance.COMPILED, "x")
        path = a.stem(
            tmp_path, (a.clip("m", "mu", 0.0, 6.0),), {"mu": music}, seconds=6.0,
            bus_=a.bus(BusRole.MUSIC), ducks=(duck,),
        )  # fmt: skip

        before, under, after = lufs(path, 0.5, 1.5), lufs(path, 2.4, 3.6), lufs(path, 4.8, 5.8)

        assert before - under == pytest.approx(12.0, abs=1.5)
        assert after - under == pytest.approx(12.0, abs=1.5)

    def test_the_duck_ramps_instead_of_stepping(self, tmp_path: Path, music: Path) -> None:
        duck = Duck("d", ("m",), "speech", 96000, 192000, 12.0, 1.0, 1.0, Provenance.COMPILED, "x")
        path = a.stem(
            tmp_path, (a.clip("m", "mu", 0.0, 6.0),), {"mu": music}, seconds=6.0,
            bus_=a.bus(BusRole.MUSIC), ducks=(duck,),
        )  # fmt: skip

        start, middle, bottom = lufs(path, 0.3, 0.7), lufs(path, 1.3, 1.7), lufs(path, 2.4, 3.6)

        assert start > middle > bottom

    def test_a_duck_acts_only_on_the_clips_it_names(
        self, tmp_path: Path, music: Path, voice: Path
    ) -> None:
        duck = Duck("d", ("m",), "speech", 0, 288000, 20.0, 0.1, 0.1, Provenance.COMPILED, "x")
        path = a.stem(
            tmp_path,
            (a.clip("m", "mu", 0.0, 3.0), a.clip("v", "vo", 3.0, 3.0)),
            {"mu": music, "vo": voice},
            seconds=6.0,
            ducks=(duck,),
        )

        assert lufs(path, 3.5, 5.5) > lufs(path, 0.5, 2.5) + 8


class TestBusProcessing:
    def test_an_eq_band_changes_the_sound_as_asked(self, tmp_path: Path, voice: Path) -> None:
        flat = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0),), {"v": voice}, seconds=4.0, name="flat.wav"
        )
        high = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0),), {"v": voice}, seconds=4.0, name="hp.wav",
            bus_=a.bus(eq=(EqBand("highpass", 4000.0),)),
        )  # fmt: skip

        assert lufs(flat, 0.5, 3.5) - lufs(high, 0.5, 3.5) > 15

    def test_a_peaking_cut_lowers_the_band(self, tmp_path: Path, voice: Path) -> None:
        flat = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0),), {"v": voice}, seconds=4.0, name="flat.wav"
        )
        cut = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0),), {"v": voice}, seconds=4.0, name="cut.wav",
            bus_=a.bus(eq=(EqBand("peaking", 440.0, -9.0, 1.0),)),
        )  # fmt: skip

        assert lufs(flat, 0.5, 3.5) - lufs(cut, 0.5, 3.5) == pytest.approx(9.0, abs=1.0)

    def test_a_compressor_reduces_the_level_of_a_loud_signal(
        self, tmp_path: Path, voice: Path
    ) -> None:
        plain = a.stem(
            tmp_path,
            (a.clip("c", "v", 0.0, 4.0, gain=6.0),),
            {"v": voice},
            seconds=4.0,
            name="p.wav",
        )
        squeezed = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0, gain=6.0),), {"v": voice}, seconds=4.0,
            name="c.wav",
            bus_=a.bus(compressor=Compressor(-40.0, 8.0, 5.0, 100.0)),
        )  # fmt: skip

        assert lufs(plain, 0.5, 3.5) - lufs(squeezed, 0.5, 3.5) > 3

    def test_bus_gain_is_part_of_the_stem(self, tmp_path: Path, voice: Path) -> None:
        zero = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0),), {"v": voice}, seconds=4.0, name="0.wav"
        )
        down = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0),), {"v": voice}, seconds=4.0, name="d.wav",
            bus_=a.bus(gain_db=-8.0),
        )  # fmt: skip

        assert lufs(zero, 0.5, 3.5) - lufs(down, 0.5, 3.5) == pytest.approx(8.0, abs=0.3)

    def test_a_hard_pan_silences_the_other_side(self, tmp_path: Path, voice: Path) -> None:
        left = a.stem(tmp_path, (a.clip("c", "v", 0.0, 4.0, pan=-1.0),), {"v": voice}, seconds=4.0)

        m = be.measure(left, a.CANCEL)

        assert m.channels == 2 and m.integrated_lufs is not None


class TestMix:
    def test_the_master_trim_moves_the_loudness_by_that_much(
        self, tmp_path: Path, voice: Path
    ) -> None:
        s = a.stem(tmp_path, (a.clip("c", "v", 0.0, 4.0, gain=-12.0),), {"v": voice}, seconds=4.0)

        base = be.measure(a.mix(tmp_path, (s,), 4.0, name="a.wav"), a.CANCEL)
        up = be.measure(a.mix(tmp_path, (s,), 4.0, trim=4.0, name="b.wav"), a.CANCEL)

        assert base.integrated_lufs is not None and up.integrated_lufs is not None
        assert up.integrated_lufs - base.integrated_lufs == pytest.approx(4.0, abs=0.4)

    def test_the_limiter_holds_the_peak_under_its_ceiling(
        self, tmp_path: Path, voice: Path
    ) -> None:
        s = a.stem(tmp_path, (a.clip("c", "v", 0.0, 4.0, gain=18.0),), {"v": voice}, seconds=4.0)

        m = be.measure(a.mix(tmp_path, (s,), 4.0, ceiling=-3.0), a.CANCEL)

        assert m.sample_peak_dbfs is not None and m.sample_peak_dbfs <= -2.9

    def test_stems_are_summed_not_normalised(
        self, tmp_path: Path, voice: Path, music: Path
    ) -> None:
        one = a.stem(
            tmp_path,
            (a.clip("c", "v", 0.0, 4.0, gain=-20.0),),
            {"v": voice},
            seconds=4.0,
            name="1.wav",
        )
        two = a.stem(
            tmp_path,
            (a.clip("c", "m", 0.0, 4.0, gain=-20.0),),
            {"m": music},
            seconds=4.0,
            name="2.wav",
        )

        single = be.measure(a.mix(tmp_path, (one,), 4.0, name="s.wav"), a.CANCEL)
        both = be.measure(a.mix(tmp_path, (one, two), 4.0, name="b.wav"), a.CANCEL)

        assert single.integrated_lufs is not None and both.integrated_lufs is not None
        assert both.integrated_lufs > single.integrated_lufs

    def test_the_delivery_format_is_what_the_file_is(self, tmp_path: Path, voice: Path) -> None:
        s = a.stem(tmp_path, (a.clip("c", "v", 0.0, 3.0),), {"v": voice}, seconds=3.0)

        stereo = be.measure(a.mix(tmp_path, (s,), 3.0, name="st.wav"), a.CANCEL)
        mono = be.measure(a.mix(tmp_path, (s,), 3.0, channels=1, name="mo.wav"), a.CANCEL)

        assert (stereo.codec, stereo.channels, stereo.sample_rate, stereo.duration_samples) == (
            "pcm_s24le",
            2,
            48000,
            144000,
        )
        assert mono.channels == 1 and mono.duration_samples == 144000

    def test_recombining_the_stems_reproduces_the_mix(
        self, tmp_path: Path, voice: Path, music: Path
    ) -> None:
        one = a.stem(
            tmp_path, (a.clip("c", "v", 0.0, 4.0),), {"v": voice}, seconds=4.0, name="1.wav"
        )
        two = a.stem(
            tmp_path, (a.clip("c", "m", 0.0, 4.0),), {"m": music}, seconds=4.0, name="2.wav"
        )

        first = a.mix(tmp_path, (one, two), 4.0, name="x.wav")
        again = a.mix(tmp_path, (one, two), 4.0, name="y.wav")

        assert be.null_test(first, again, a.CANCEL) == -200.0

    def test_different_mixes_are_not_the_same(self, tmp_path: Path, voice: Path) -> None:
        s = a.stem(tmp_path, (a.clip("c", "v", 0.0, 4.0, gain=-12.0),), {"v": voice}, seconds=4.0)

        one = a.mix(tmp_path, (s,), 4.0, name="x.wav")
        other = a.mix(tmp_path, (s,), 4.0, trim=6.0, name="y.wav")

        diff = be.null_test(one, other, a.CANCEL)
        assert diff is not None and diff > -60.0


class TestMeasuring:
    def test_a_known_tone_has_a_known_peak(self, tmp_path: Path, voice: Path) -> None:
        m = be.measure(voice, a.CANCEL)

        # FFmpeg's sine is -18 dBFS and a mono-to-stereo upmix takes 3 dB: measured, not assumed
        assert m.sample_peak_dbfs is not None and -22.0 < m.sample_peak_dbfs < -20.0
        assert m.integrated_lufs is not None and m.true_peak_dbtp is not None

    def test_silence_cannot_be_measured_and_says_so(self, tmp_path: Path) -> None:
        quiet = tmp_path / "q.wav"
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "2",
            "-c:a",
            "pcm_s16le",
            str(quiet),
        )

        m = be.measure(quiet, a.CANCEL)

        assert m.integrated_lufs is None and m.loudness_range_lu is None

    def test_a_part_too_short_to_measure_is_none(self, voice: Path) -> None:
        assert be.measure_range(voice, 0.0, 0.1, a.CANCEL) is None

    def test_loudness_over_several_ranges(self, tmp_path: Path, voice: Path) -> None:
        s = a.stem(
            tmp_path,
            (a.clip("c", "v", 1.0, 1.0), a.clip("d", "v", 3.0, 1.0)),
            {"v": voice},
            seconds=5.0,
        )

        inside = be.loudness_over(s, [(1.0, 2.0), (3.0, 4.0)], a.CANCEL)
        outside = be.loudness_over(s, [(0.0, 0.8), (4.2, 5.0)], a.CANCEL)

        assert inside is not None and outside is None

    def test_a_missing_file_is_a_backend_failure_that_names_the_operation(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(BackendFailed, match="measure the loudness"):
            be.measure(tmp_path / "nope.wav", a.CANCEL)

    def test_a_missing_source_fails_the_render_and_leaves_no_file(self, tmp_path: Path) -> None:
        with pytest.raises(BackendFailed, match="render the dialogue stem"):
            a.stem(
                tmp_path, (a.clip("c", "v", 0.0, 1.0),), {"v": tmp_path / "gone.wav"}, seconds=2.0
            )


class TestEffectLoudness:
    def test_a_short_effect_is_measured_by_its_loudest_moment_not_integrated_away(
        self, tmp_path: Path
    ) -> None:
        pop = a.tone(tmp_path / "pop.wav", 0.15, volume=0.5)

        assert be.peak_loudness(pop, 0.0, 0.15, a.CANCEL) is not None

    def test_the_loudest_moment_follows_the_level_of_the_effect(self, tmp_path: Path) -> None:
        loud = a.tone(tmp_path / "loud.wav", 1.0, volume=0.5)
        soft = a.tone(tmp_path / "soft.wav", 1.0, volume=0.05)

        louder = be.peak_loudness(loud, 0.0, 1.0, a.CANCEL)
        softer = be.peak_loudness(soft, 0.0, 1.0, a.CANCEL)

        assert louder is not None and softer is not None
        assert louder - softer == pytest.approx(20.0, abs=0.5)

    def test_silence_has_no_loudness(self, tmp_path: Path) -> None:
        quiet = a.tone(tmp_path / "quiet.wav", 0.5, volume=0.0)

        assert be.peak_loudness(quiet, 0.0, 0.5, a.CANCEL) is None
