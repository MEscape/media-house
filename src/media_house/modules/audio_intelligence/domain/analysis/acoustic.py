"""Measured acoustic data: the continuous frame timeline and timestamped events.

Frame ``i`` covers ``[origin + i*hop, origin + (i+1)*hop)`` on the ORIGINAL media timeline.
Columns are compact ``array('d')``. ``NaN`` means "not measured / not applicable" (e.g. ``f0``
of an unvoiced frame) and is serialised as ``null``; it is never silently turned into 0.
"""

import math
from array import array
from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.shared.errors import InvariantViolation

# --- event vocabulary (``AudioEvent.kind`` is a plain string so detectors can add their own) ----
PITCH_RISE = "pitch_rise"
PITCH_FALL = "pitch_fall"
ENERGY_SPIKE = "energy_spike"
ENERGY_DROP = "energy_drop"
SILENCE = "silence"
PAUSE = "pause"
LAUGHTER = "laughter"
BREATH = "breath"
NONVERBAL_KINDS = frozenset({LAUGHTER, BREATH, "scream", "cry", "sigh", "music", "noise"})


@dataclass(frozen=True, slots=True)
class AudioEvent:
    """Something that happened acoustically, with a time span and a 0..1 ``strength``.

    ``strength`` is how pronounced the phenomenon is (baseline-independent magnitude mapped
    to [0, 1], see ``AnalysisConfig`` full scales). ``confidence`` is the DETECTOR's own
    reliability when it provides one; it is ``None`` otherwise and never invented.
    """

    kind: str
    start: float
    end: float
    strength: float
    confidence: float | None = None
    #: Which analyzer/detector produced it (and therefore which version to blame).
    source: str = ""
    details: Mapping[str, float] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2


def _column(values: array[float] | list[float] | tuple[float, ...]) -> array[float]:
    return values if isinstance(values, array) else array("d", values)


@dataclass(frozen=True, slots=True)
class AcousticTrack:
    """Frame-synchronous measurements. All columns have the same length."""

    origin: float
    hop: float
    #: Fundamental frequency in Hz; NaN = unvoiced / no reliable pitch.
    f0: array[float]
    #: Voicing strength of the pitch estimate in [0, 1] (the tracker's own); NaN = unknown.
    pitch_confidence: array[float]
    #: Frame RMS in dBFS (digital silence is floored at ``SILENCE_FLOOR_DB``).
    rms_db: array[float]
    #: Momentary K-weighted loudness (LUFS-style, 400 ms window); NaN = unknown.
    loudness: array[float]
    #: 1 = speech activity (energy well above the noise floor or voiced), 0 = none.
    speech: array[float]

    def __post_init__(self) -> None:
        if self.hop <= 0 or not math.isfinite(self.origin):
            raise InvariantViolation("Acoustic track needs a positive hop and a finite origin")
        sizes = {len(c) for c in self.columns().values()}
        if len(sizes) > 1:
            raise InvariantViolation("Acoustic track columns must have equal length")

    def columns(self) -> dict[str, array[float]]:
        return {
            "f0": self.f0,
            "pitch_confidence": self.pitch_confidence,
            "rms_db": self.rms_db,
            "loudness": self.loudness,
            "speech": self.speech,
        }

    @classmethod
    def of(
        cls,
        *,
        origin: float,
        hop: float,
        f0: array[float] | list[float],
        pitch_confidence: array[float] | list[float],
        rms_db: array[float] | list[float],
        loudness: array[float] | list[float],
        speech: array[float] | list[float],
    ) -> "AcousticTrack":
        return cls(
            origin,
            hop,
            _column(f0),
            _column(pitch_confidence),
            _column(rms_db),
            _column(loudness),
            _column(speech),
        )

    def __len__(self) -> int:
        return len(self.f0)

    @property
    def duration(self) -> float:
        return len(self) * self.hop

    @property
    def end(self) -> float:
        return self.origin + self.duration

    def frame_start(self, index: int) -> float:
        return self.origin + index * self.hop

    def index_at(self, timestamp: float) -> int | None:
        """The frame containing ``timestamp``, or ``None`` outside the track."""
        index = math.floor((timestamp - self.origin) / self.hop)
        return index if 0 <= index < len(self) else None

    def span(self, start: float, end: float) -> range:
        """Frames whose CENTRE lies in ``[start, end)`` (so adjacent spans never share a frame)."""
        first = max(0, math.ceil((start - self.origin) / self.hop - 0.5))
        last = min(len(self), math.ceil((end - self.origin) / self.hop - 0.5))
        return range(first, max(first, last))

    def is_voiced(self, index: int, min_confidence: float) -> bool:
        return math.isfinite(self.f0[index]) and self.pitch_confidence[index] >= min_confidence


@dataclass(frozen=True, slots=True)
class AcousticMeasurements:
    """Everything measured from the audio, before any interpretation. Reusable on its own."""

    track: AcousticTrack
    #: ``{analyzer: version}`` e.g. ``{"pitch": "parselmouth-0.4.7/praat-6.1.38"}``.
    identity: Mapping[str, str]
    #: Parameters the analyzers actually used (e.g. the pitch range found by the first pass).
    parameters: Mapping[str, float] = field(default_factory=dict)
    #: Events from pluggable non-verbal detectors (laughter, breath, ...).
    events: tuple[AudioEvent, ...] = ()
    warnings: tuple[str, ...] = ()


SILENCE_FLOOR_DB = -120.0
