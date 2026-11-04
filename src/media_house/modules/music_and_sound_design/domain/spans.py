"""Small exact helpers shared by the planners: time spans, frames, asset references, energy."""

import hashlib
import json
from collections.abc import Iterable
from fractions import Fraction

from media_house.modules.music_and_sound_design.domain.analysis import MusicAnalysis
from media_house.modules.music_and_sound_design.domain.library import SoundAsset
from media_house.modules.music_and_sound_design.domain.model import AssetRef

type Span = tuple[Fraction, Fraction]

#: Scales of the measured-energy heuristic (documented in the README): onset rate, tempo,
#: integrated loudness. Measurement ranges, not creative choices.
_ONSET_RATE_RANGE = (1.0, 8.0)
_TEMPO_RANGE = (60.0, 180.0)
_LOUDNESS_RANGE = (-30.0, -8.0)


def frame(t: Fraction, fps: Fraction) -> int:
    """Frames are computed once, at the edge: ``round(t * fps)`` (half to even)."""
    return round(t * fps)


def seconds(value: float) -> Fraction:
    """A configured duration (float seconds) as an exact value (millisecond grid)."""
    return Fraction(round(value * 1000), 1000)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def merge(spans: Iterable[Span], gap: Fraction = Fraction(0)) -> list[Span]:
    """Sorted spans with overlaps (and gaps up to ``gap``) joined."""
    out: list[Span] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if out and start - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def overlap(a: Span, b: Span) -> Span | None:
    start, end = max(a[0], b[0]), min(a[1], b[1])
    return (start, end) if end > start else None


def overlaps_any(span: Span, spans: Iterable[Span]) -> bool:
    return any(overlap(span, s) is not None for s in spans)


def subtract(span: Span, holes: Iterable[Span]) -> list[Span]:
    """``span`` minus every hole (sorted, non-empty pieces)."""
    pieces = [span]
    for hole in merge(holes):
        next_pieces: list[Span] = []
        for start, end in pieces:
            if hole[1] <= start or hole[0] >= end:
                next_pieces.append((start, end))
                continue
            if hole[0] > start:
                next_pieces.append((start, hole[0]))
            if hole[1] < end:
                next_pieces.append((hole[1], end))
        pieces = next_pieces
    return pieces


def asset_ref(sound: SoundAsset) -> AssetRef:
    return AssetRef(
        asset_id=sound.asset_id,
        kind=sound.kind,
        title=sound.title,
        analysis_asset_id=sound.analysis_asset_id,
        licence_state=sound.licence.state,
        licence_name=sound.licence.name,
        attribution_required=sound.licence.attribution_required,
    )


def _scale(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return min(1.0, max(0.0, (value - low) / (high - low)))


def track_energy(analysis: MusicAnalysis | None) -> float | None:
    """0..1 measured energy: onset rate (0.4), tempo (0.3) and loudness (0.3).

    A heuristic over MEASURED values (no mood is inferred); ``None`` without a measurement.
    Missing parts are left out and the weights renormalised.
    """
    if analysis is None:
        return None
    m = analysis.measurement
    parts = [(0.4, _scale(m.onset_rate, _ONSET_RATE_RANGE))]
    if m.tempo is not None:
        parts.append((0.3, _scale(m.tempo.bpm, _TEMPO_RANGE)))
    if m.loudness.integrated_lufs is not None:
        parts.append((0.3, _scale(m.loudness.integrated_lufs, _LOUDNESS_RANGE)))
    total = sum(w for w, _ in parts)
    return round(sum(w * v for w, v in parts) / total, 3)
