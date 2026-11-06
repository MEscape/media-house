"""Validate a specification before anything is rendered: structure, routing, timing, and whether
the backend can do what the specification needs.

An unsupported operation is an ERROR that names it. Nothing is silently replaced by a processor
of another kind.
"""

from collections.abc import Callable, Collection
from itertools import pairwise

from media_house.modules.audio_mix_engine.domain.spec import AudioMixSpecification, SpecIssue
from media_house.modules.audio_mix_engine.domain.values import MASTER, BusRole, Severity

E, W, I = Severity.ERROR, Severity.WARNING, Severity.INFO  # noqa: E741

type Adder = Callable[[str, Severity, str, str | None], None]

_EQ_OPERATION = {
    "highpass": "eq.highpass",
    "lowpass": "eq.lowpass",
    "peaking": "eq.peaking",
    "lowshelf": "eq.lowshelf",
    "highshelf": "eq.highshelf",
    "notch": "eq.notch",
}

#: Backend-neutral names of everything the engine can ask of a processing backend.
ALL_OPERATIONS = frozenset(
    {
        "trim", "gain", "fade", "delay", "sum", "resample", "pan", "tempo", "envelope",
        "compressor", "limiter", "measure.loudness", "measure.range", "measure.level",
        "silence.detect", "null_test", *_EQ_OPERATION.values(),
    }
)  # fmt: skip


def validate_spec(spec: AudioMixSpecification) -> tuple[SpecIssue, ...]:
    found: list[SpecIssue] = []

    def add(code: str, severity: Severity, message: str, element: str | None = None) -> None:
        found.append(SpecIssue(code, severity, message, element))

    _unique(spec, add)
    buses = {b.bus_id: b for b in spec.buses}
    masters = [b for b in spec.buses if b.role is BusRole.MASTER]
    if len(masters) != 1:
        add("master_bus", E, f"a specification needs exactly one master bus, not {len(masters)}")
    for bus in spec.buses:
        if bus.role is BusRole.MASTER:
            continue
        if bus.parent is None or bus.parent not in buses:
            add(
                "bus_destination",
                E,
                f"bus {bus.bus_id} feeds {bus.parent!r}, which does not exist",
                bus.bus_id,
            )
        elif bus.parent != MASTER:
            add(
                "bus_destination",
                E,
                f"bus {bus.bus_id} must feed the master, not {bus.parent}",
                bus.bus_id,
            )
    tracks = {t.track_id: t for t in spec.tracks}
    for t in spec.tracks:
        if t.bus_id not in buses or t.bus_id == MASTER:
            add("track_unrouted", E, f"track {t.track_id} is routed to {t.bus_id!r}", t.track_id)
    routed = {t.bus_id for t in spec.tracks}
    for bus in spec.buses:
        if bus.role is not BusRole.MASTER and bus.bus_id not in routed:
            add("bus_without_input", W, f"bus {bus.bus_id} has no track feeding it", bus.bus_id)
    sources = {s.source_id for s in spec.sources}
    total = spec.timebase.duration_samples
    for c in spec.clips:
        if c.track_id not in tracks:
            add(
                "clip_track",
                E,
                f"{c.clip_id} is on a track the specification does not have",
                c.clip_id,
            )
        if c.source_id not in sources:
            add(
                "clip_source",
                E,
                f"{c.clip_id} plays a source the specification does not list",
                c.clip_id,
            )
        if c.end_sample <= c.start_sample:
            add("clip_length", E, f"{c.clip_id} has no length", c.clip_id)
        if c.end_sample > total + 1:
            add("clip_beyond_timeline", E, f"{c.clip_id} ends after the timeline does", c.clip_id)
        if (
            c.source_out.numerator * c.source_in.denominator
            <= c.source_in.numerator * c.source_out.denominator
        ):
            add("clip_range", E, f"{c.clip_id} plays no part of its file", c.clip_id)
        if c.speed.numerator <= 0:
            add("clip_speed", E, f"{c.clip_id} has no speed", c.clip_id)
        length = (c.end_sample - c.start_sample) / spec.timebase.sample_rate
        fades = c.fade_in.value + c.fade_out.value
        if fades > length + 1e-9:
            add("fades_overlap", W, f"the fades of {c.clip_id} are longer than the clip", c.clip_id)
    _overlaps(spec, add)
    clip_ids = {c.clip_id for c in spec.clips}
    for d in spec.ducking:
        if not d.clip_ids or any(i not in clip_ids for i in d.clip_ids):
            add(
                "duck_target",
                E,
                f"ducking {d.duck_id} acts on a clip that is not in the mix",
                d.duck_id,
            )
        if d.end_sample <= d.start_sample or d.depth_db < 0:
            add("duck_range", E, f"ducking {d.duck_id} has no range or a negative depth", d.duck_id)
    for a in spec.automation:
        samples = [p.sample for p in a.points]
        if samples != sorted(set(samples)):
            add(
                "automation_order",
                E,
                f"automation {a.automation_id} is not in time order",
                a.automation_id,
            )
        known = clip_ids if a.target_kind == "clip" else set(buses)
        if a.target_id not in known:
            add(
                "automation_target",
                E,
                f"automation {a.automation_id} acts on {a.target_id}, which is not there",
                a.automation_id,
            )
    for stem in spec.stems:
        if stem.bus_id not in buses or stem.bus_id == MASTER:
            add("stem_bus", E, f"stem {stem.stem_id} is of bus {stem.bus_id!r}", stem.stem_id)
    if spec.render.channels not in (1, 2) or spec.render.sample_rate <= 0:
        add("render_format", E, "the render format is not mono or stereo at a positive rate")
    if spec.timebase.sample_rate <= 0 or spec.timebase.duration_samples <= 0:
        add("timebase", E, "the timebase has no rate or no length")
    for s in spec.sources:
        if s.sample_rate is None:
            add(
                "source_rate_unknown",
                I,
                f"the sample rate of {s.source_id} is not known; it is trimmed by time",
                s.source_id,
            )
    return tuple(found)


def _unique(spec: AudioMixSpecification, add: Adder) -> None:
    for label, ids in (
        ("clip", [c.clip_id for c in spec.clips]),
        ("track", [t.track_id for t in spec.tracks]),
        ("bus", [b.bus_id for b in spec.buses]),
        ("duck", [d.duck_id for d in spec.ducking]),
    ):
        seen: set[str] = set()
        for i in ids:
            if i in seen:
                add(f"duplicate_{label}", E, f"{label} id {i} appears twice", i)
            seen.add(i)


def _overlaps(spec: AudioMixSpecification, add: Adder) -> None:
    crossed = {i for x in spec.crossfades for i in x.clip_ids}
    by_track: dict[str, list[tuple[int, int, str]]] = {}
    for c in spec.clips:
        by_track.setdefault(c.track_id, []).append((c.start_sample, c.end_sample, c.clip_id))
    for track, spans in by_track.items():
        spans.sort()
        for (_, e1, a), (s2, _, b) in pairwise(spans):
            if s2 < e1 and not ({a, b} <= crossed):
                add("clip_overlap", W, f"{a} and {b} overlap on {track}", a)


# --- what the backend must be able to do -------------------------------------------------------


def required_operations(spec: AudioMixSpecification) -> frozenset[str]:
    ops = {
        "trim",
        "gain",
        "delay",
        "sum",
        "resample",
        "measure.loudness",
        "measure.range",
        "measure.level",
    }
    ops |= {"silence.detect", "null_test"}
    if any(c.fade_in.numerator > 0 or c.fade_out.numerator > 0 for c in spec.clips):
        ops.add("fade")
    if any(c.speed.numerator != c.speed.denominator for c in spec.clips):
        ops.add("tempo")
    if any(c.pan != 0.0 for c in spec.clips) or any(b.pan != 0.0 for b in spec.buses):
        ops.add("pan")
    if spec.ducking or spec.automation:
        ops.add("envelope")
    for bus in spec.buses:
        ops |= {_EQ_OPERATION[band.kind] for band in bus.eq if band.kind in _EQ_OPERATION}
        if bus.compressor is not None:
            ops.add("compressor")
        if bus.limiter is not None:
            ops.add("limiter")
    return frozenset(ops)


def check_capabilities(
    spec: AudioMixSpecification, supported: Collection[str]
) -> tuple[SpecIssue, ...]:
    missing = sorted(required_operations(spec) - set(supported))
    return tuple(
        SpecIssue(
            "operation_unsupported",
            E,
            f"the audio backend cannot do {op}, which this mix needs",
            op,
        )
        for op in missing
    )
