"""What was MEASURED about one library sound: the ``music_analysis`` and ``sfx_profile`` documents.

Only measurements live here. What the source says about a file (mood, genre, stated BPM,
licence) stays in the asset metadata and is labelled "stated" wherever it is used; a mood or
genre is never measured and never invented. A value that cannot be measured is listed in
``not_available`` with the reason instead of being guessed.

Positions are exact: beats, sections and edit points are analysis-frame indices with the
frame rate as a ``Rational`` (``seconds = frame / frame_rate``); SFX points are sample indices
at ``sample_rate``. Durations, levels and scores are measurements (floats).
"""

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.music_and_sound_design.domain.values import (
    NOT_AVAILABLE,
    EditPointKind,
    SectionLabel,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Unavailable:
    what: str
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Loudness:
    """FFmpeg ``ebur128`` (ITU-R BS.1770 / EBU R128). ``None`` = below the gate / too short."""

    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbtp: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Tempo:
    bpm: float
    #: 0..1: how much the chosen periodicity stands out in the onset autocorrelation.
    confidence: float
    #: 0..1: share of 20 s windows whose own tempo agrees with ``bpm`` within 4 %.
    stability: float
    #: Half and double tempo: the usual octave ambiguity, for a caller that knows better.
    octave_alternatives: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class MusicSection:
    start_frame: int
    end_frame: int
    label: SectionLabel
    #: Mean RMS level of the section (dBFS).
    energy_db: float
    #: The measurement that justifies the label ("" for ``unlabeled``).
    evidence: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class LoopRegion:
    """Jumping from ``end_frame`` back to ``start_frame`` (both downbeats) sounds seamless."""

    start_frame: int
    end_frame: int
    #: Cosine similarity of the four bars after each point (1 = identical).
    similarity: float


@dataclass(frozen=True, slots=True, kw_only=True)
class EditPoint:
    kind: EditPointKind
    frame: int
    strength: float


@dataclass(frozen=True, slots=True, kw_only=True)
class MusicMeasurement:
    """What the measurer returns for one track (no identity, no time stamp)."""

    duration_seconds: float
    #: Analysis frames per second (sample rate / hop).
    frame_rate: Rational
    loudness: Loudness
    #: Spectral-flux onset strength per analysis frame (normalised, 3 decimals).
    onset_envelope: tuple[float, ...]
    #: Onset peaks per second: a measured, comparable rhythmic-activity figure.
    onset_rate: float
    tempo: Tempo | None
    beat_frames: tuple[int, ...]
    downbeat_frames: tuple[int, ...]
    downbeat_confidence: float | None
    #: Downbeats assume this many beats per bar (no meter detector exists here).
    meter_assumed: int
    #: RMS level (dBFS) at ``energy_rate`` values per second.
    energy_rate: Rational
    energy_db: tuple[float, ...]
    sections: tuple[MusicSection, ...]
    loop: LoopRegion | None
    edit_points: tuple[EditPoint, ...]
    vocal_presence: str = NOT_AVAILABLE
    not_available: tuple[Unavailable, ...] = ()
    warnings: tuple[str, ...] = ()
    engines: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class MusicAnalysis:
    """The stored ``music_analysis`` document of one music asset."""

    analysis_id: str
    asset_id: str
    checksum: str
    created_at: datetime
    analyzer_version: int
    measurement: MusicMeasurement
    schema_version: int

    def seconds(self, frame: int) -> Fraction:
        rate = self.measurement.frame_rate
        return Fraction(frame) * Fraction(rate.denominator, rate.numerator)

    @property
    def loopable(self) -> bool:
        return self.measurement.loop is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class SfxMeasurement:
    duration_seconds: float
    sample_rate: int
    loudness: Loudness
    peak_dbfs: float
    #: Loudest 50 ms RMS (dBFS): the level a short sound is perceived at.
    rms_max_dbfs: float
    onset_sample: int
    peak_sample: int
    useful_start_sample: int
    useful_end_sample: int
    attack_seconds: float
    tail_seconds: float
    spectral_centroid_hz: float
    #: Share of the energy above 4 kHz.
    brightness: float
    not_available: tuple[Unavailable, ...] = ()
    warnings: tuple[str, ...] = ()
    engines: tuple[tuple[str, str], ...] = ()

    def seconds(self, sample: int) -> Fraction:
        return Fraction(sample, self.sample_rate)


@dataclass(frozen=True, slots=True, kw_only=True)
class SfxProfile:
    """The stored ``sfx_profile`` document of one sound-effect asset."""

    analysis_id: str
    asset_id: str
    checksum: str
    created_at: datetime
    analyzer_version: int
    measurement: SfxMeasurement
    schema_version: int
