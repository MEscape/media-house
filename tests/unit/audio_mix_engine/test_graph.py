"""The FFmpeg graph text: what is asked of the tool, readable without running it."""

from dataclasses import replace
from pathlib import Path

from media_house.modules.audio_mix_engine.application.ports import MixRequest, StemRequest
from media_house.modules.audio_mix_engine.domain.profile import Compressor, EqBand, Limiter
from media_house.modules.audio_mix_engine.domain.spec import Duck, MixBus, RenderConfig
from media_house.modules.audio_mix_engine.domain.values import BusRole, Provenance
from media_house.modules.audio_mix_engine.infrastructure.ffmpeg_graph import (
    duck_expression,
    mix_graph,
    stem_graph,
)
from tests.unit.audio_mix_engine.audio_support import RATE, bus, clip


def request(
    *clips: object, rates: dict[str, int | None] | None = None, **kw: object
) -> StemRequest:
    base = StemRequest(
        bus=bus(),
        clips=tuple(clips),  # type: ignore[arg-type]
        ducks=(),
        automation=(),
        trims={},
        sources={"s": Path("s.wav"), "t": Path("t.wav")},
        source_rates=rates or {"s": RATE, "t": RATE},
        sample_rate=RATE,
        timebase_rate=RATE,
        duration_samples=RATE * 10,
        destination=Path("out.wav"),
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def test_a_clip_is_cut_by_sample_when_the_files_rate_is_known_and_by_time_when_not() -> None:
    _, known = stem_graph(request(clip("a", "s", 0.0, 1.0, src_in=2.0)))
    _, unknown = stem_graph(request(clip("a", "s", 0.0, 1.0, src_in=2.0), rates={"s": None}))

    assert "atrim=start_sample=96000:end_sample=144000" in known
    assert "atrim=start=2.000000:end=3.000000" in unknown


def test_the_clip_is_delayed_to_its_place_in_samples_and_padded_to_its_length() -> None:
    _, graph = stem_graph(request(clip("a", "s", 1.5, 2.0)))

    assert (
        "adelay=delays=72000S:all=1" in graph
        and "apad=whole_len=96000,atrim=end_sample=96000" in graph
    )


def test_a_clip_at_the_start_needs_no_delay_and_no_gain_filter_at_zero_db() -> None:
    _, graph = stem_graph(request(clip("a", "s", 0.0, 2.0)))

    assert "adelay" not in graph and "volume=" not in graph


def test_gain_and_the_dialogue_trim_are_one_volume() -> None:
    _, graph = stem_graph(request(clip("a", "s", 0.0, 2.0, gain=-3.0), trims={"a": 1.5}))

    assert "volume=-1.500dB" in graph


def test_fades_follow_the_clips_curve() -> None:
    _, linear = stem_graph(request(clip("a", "s", 0.0, 2.0, fade_in=0.5)))
    _, equal = stem_graph(request(clip("a", "s", 0.0, 2.0, fade_out=0.5, curve="equal_power")))

    assert "afade=t=in:st=0:d=0.500000:curve=tri" in linear
    assert "afade=t=out:st=1.500000:d=0.500000:curve=qsin" in equal


def test_sources_are_inputs_once_in_a_stable_order() -> None:
    order, graph = stem_graph(
        request(clip("a", "t", 0.0, 1.0), clip("b", "s", 1.0, 1.0), clip("c", "t", 2.0, 1.0))
    )

    assert order == ["s", "t"]
    assert "[1:a]atrim" in graph and "[0:a]atrim" in graph


def test_clips_are_summed_without_normalising_and_a_single_clip_is_not_mixed() -> None:
    _, two = stem_graph(request(clip("a", "s", 0.0, 1.0), clip("b", "s", 2.0, 1.0)))
    _, one = stem_graph(request(clip("a", "s", 0.0, 1.0)))

    assert "amix=inputs=2:normalize=0" in two and "amix" not in one


def test_a_faster_clip_is_a_tempo_chain_within_the_filters_range() -> None:
    _, graph = stem_graph(request(clip("a", "s", 0.0, 1.0, speed=5.0)))

    assert graph.count("atempo=2.0") == 2 and "atempo=1.25" in graph


def test_the_bus_chain_runs_in_a_fixed_order() -> None:
    b = bus(
        BusRole.MUSIC,
        gain_db=-4.0,
        eq=(EqBand("highpass", 40.0), EqBand("peaking", 2500.0, -2.5, 0.9)),
        compressor=Compressor(-18.0, 2.0, 10.0, 100.0),
    )
    _, graph = stem_graph(request(clip("a", "s", 0.0, 1.0), bus=b))

    positions = [
        graph.index(x)
        for x in ("volume=-4.000dB", "highpass=f=40", "equalizer=f=2500", "acompressor=")
    ]
    assert positions == sorted(positions)


def test_a_duck_is_a_ramped_envelope_on_the_clips_it_names_only() -> None:
    duck = Duck("d", ("a",), "speech", 48000, 96000, 12.0, 0.2, 0.4, Provenance.COMPILED, "x")
    _, graph = stem_graph(
        request(clip("a", "s", 0.0, 3.0), clip("b", "s", 3.0, 3.0), ducks=(duck,))
    )

    first, second = [line for line in graph.split(";\n") if line.startswith("[0:a]")]
    assert "eval=frame" in first and "eval=frame" not in second


def test_the_deepest_duck_wins_and_the_expression_stays_shallow_for_many_ducks() -> None:
    ducks = [
        Duck(
            f"d{i}",
            ("a",),
            "speech",
            i * 48000,
            i * 48000 + 24000,
            9.0,
            0.2,
            0.4,
            Provenance.COMPILED,
            "x",
        )
        for i in range(300)
    ]
    expression = duck_expression(ducks, RATE)

    assert expression.count("min(") >= 299 and max(_depth(expression), 1) < 60


def _depth(text: str) -> int:
    depth = best = 0
    for ch in text:
        depth += ch == "("
        best = max(best, depth)
        depth -= ch == ")"
    return best


def test_the_mix_sums_the_stems_then_the_masters_chain() -> None:
    master = MixBus(
        "master",
        BusRole.MASTER,
        "Master",
        None,
        0.0,
        eq=(EqBand("highpass", 30.0),),
        limiter=Limiter(-1.5),
    )
    graph = mix_graph(
        MixRequest(
            (Path("a.wav"), Path("b.wav")),
            master,
            2.5,
            RenderConfig(RATE, 24, 2, "wav", "pcm_s24le"),
            RATE * 10,
            Path("m.wav"),
        )
    )

    assert graph.startswith("[0:a][1:a]amix=inputs=2:normalize=0")
    assert (
        graph.index("volume=2.500dB")
        < graph.index("highpass=f=30")
        < graph.index("alimiter=limit=0.84")
    )
    assert "pan=mono" not in graph and graph.endswith("[out]")


def test_a_mono_delivery_folds_the_stereo_mix_down() -> None:
    master = MixBus("master", BusRole.MASTER, "Master", None, 0.0)
    graph = mix_graph(
        MixRequest(
            (Path("a.wav"),),
            master,
            0.0,
            RenderConfig(RATE, 16, 1, "wav", "pcm_s16le"),
            RATE,
            Path("m.wav"),
        )
    )

    assert "pan=mono|c0=0.5*c0+0.5*c1" in graph
