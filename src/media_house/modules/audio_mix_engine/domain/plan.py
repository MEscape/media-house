"""What has to be rendered again, and by how much to correct levels.

Every output has a hash of everything that can change it: the sources' content, the clips and
their settings, the bus chain, the ducking that acts on it, the timebase and the backend. A stem is
reused only if that hash is unchanged, so changing the dialogue never re-renders the music, and a
different loudness target re-renders only the final mix. Never a filename, never a timestamp.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from media_house.core.domain import json_codec
from media_house.modules.audio_mix_engine.domain.spec import AudioMixSpecification, Loudness
from media_house.modules.audio_mix_engine.domain.values import MASTER

#: The largest correction applied to reach the loudness target in one step (dB).
MAX_BOOST_DB = 9.0
MAX_CUT_DB = 12.0
#: A dialogue trim smaller than this is not worth applying (dB).
MIN_TRIM_DB = 0.5


def stem_hash(
    spec: AudioMixSpecification,
    bus_id: str,
    checksums: Mapping[str, str | None],
    trims: Mapping[str, float],
    backend: str,
) -> str:
    """Everything that can change the stem of one bus."""
    bus = spec.bus(bus_id)
    clips = spec.clips_of(bus_id)
    ids = {c.clip_id for c in clips}
    sources = sorted({c.source_id for c in clips})
    return json_codec.digest(
        {
            "bus": bus,
            "tracks": [t for t in spec.tracks if t.bus_id == bus_id],
            "clips": clips,
            "trims": {i: trims.get(i, 0.0) for i in sorted(ids)},
            "sources": [(s, checksums.get(s), spec.source(s)) for s in sources],
            "ducks": [d for d in spec.ducking if ids & set(d.clip_ids)],
            "automation": [
                a for a in spec.automation
                if (a.target_kind == "bus" and a.target_id == bus_id)
                or (a.target_kind == "clip" and a.target_id in ids)
            ],
            "rate": spec.render.sample_rate,
            "timebase": spec.timebase,
            "backend": backend,
        }
    )  # fmt: skip


def mix_hash(spec: AudioMixSpecification, stems: Mapping[str, str], backend: str) -> str:
    """Everything that can change the final mix, given the stems it is made of."""
    return json_codec.digest(
        {
            "stems": sorted(stems.items()),
            "master": spec.bus(MASTER),
            "render": spec.render,
            "loudness": spec.loudness,
            "duration": spec.timebase.duration_samples,
            "backend": backend,
        }
    )


def dialogue_trims(
    measured: Mapping[str, float | None],
    *,
    target_lufs: float,
    levelling: float,
    max_trim_db: float,
) -> dict[str, float]:
    """A gentle step toward the profile's dialogue loudness, per clip. Only a fraction of the way
    (natural differences between recordings and speakers survive), never more than the limit,
    and nothing for a clip that could not be measured."""
    found: dict[str, float] = {}
    for clip_id, lufs in sorted(measured.items()):
        if lufs is None:
            continue
        trim = max(-max_trim_db, min(max_trim_db, (target_lufs - lufs) * levelling))
        trim = round(trim, 1)
        if abs(trim) >= MIN_TRIM_DB:
            found[clip_id] = trim
    return found


def loudness_correction(measured_lufs: float | None, loudness: Loudness) -> float | None:
    """The master trim (dB) that brings the mix to the target, or ``None`` when it is already
    within tolerance (or cannot be measured). Bounded: a mix is not squashed to reach a number."""
    if measured_lufs is None:
        return None
    delta = loudness.integrated_lufs - measured_lufs
    if abs(delta) <= loudness.tolerance_lu:
        return None
    return round(max(-MAX_CUT_DB, min(MAX_BOOST_DB, delta)), 2)


@dataclass(frozen=True, slots=True)
class StagePlan:
    stem_hashes: Mapping[str, str]
    mix_hash: str
