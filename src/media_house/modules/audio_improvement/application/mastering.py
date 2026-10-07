"""Mastering: gain to the loudness target, then a true-peak limiter, verified by measurement.

Shared by single-recording improvement and by mixing (mastering always happens AFTER the mix).
Limiting lowers loudness and a limiter may overshoot its ceiling, so each pass is re-measured
and refined; audio that is already on target is left bit-exact.
"""

from dataclasses import dataclass
from pathlib import Path

from media_house.modules.audio_improvement.application.ports import QualityAnalyzer, StageProcessor
from media_house.modules.audio_improvement.domain.measurements import QualityMeasurements
from media_house.modules.audio_improvement.domain.provenance import StageRecord
from media_house.modules.audio_improvement.domain.settings import MasteringSettings
from media_house.modules.audio_improvement.domain.values import ProcessingStage, StageStatus
from media_house.modules.audio_improvement.domain.verification import meets_delivery
from media_house.shared.concurrency import CancellationToken
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MasteringOutcome:
    #: The file to continue with (the input itself when nothing had to be done).
    path: Path
    record: StageRecord
    measurements: QualityMeasurements


class Mastering:
    """Loudness + true-peak stage built on a ``MASTERING`` engine and the quality analyzer."""

    def __init__(self, engine: StageProcessor, analyzer: QualityAnalyzer) -> None:
        self._engine = engine
        self._analyzer = analyzer

    def master(
        self,
        source: Path,
        workdir: Path,
        current: QualityMeasurements,
        settings: MasteringSettings,
        cancellation: CancellationToken,
    ) -> MasteringOutcome:
        stage = ProcessingStage.MASTERING
        if current.integrated_lufs is None:
            return self._unchanged(source, current, "loudness not measurable (silent or too short)")
        if settings.skip_if_compliant and meets_delivery(current, settings):
            return self._unchanged(
                source,
                current,
                f"already on target ({current.integrated_lufs:.1f} LUFS, "
                f"{current.true_peak_dbtp} dBTP)",
            )

        limit = settings.max_gain_db
        gain = max(-limit, min(limit, settings.target_lufs - current.integrated_lufs))
        ceiling = settings.true_peak_ceiling_dbtp
        passes = 0
        output, measured = source, current
        for passes in range(1, settings.max_passes + 1):
            cancellation.raise_if_cancelled()
            output = workdir / f"mastered-{passes}.wav"
            params = {
                "gain_db": gain,
                "ceiling_dbtp": ceiling,
                "release_ms": settings.limiter_release_ms,
            }
            self._engine.process(source, output, params, cancellation)
            measured = self._analyzer.analyze(output, cancellation)
            if measured.integrated_lufs is None or meets_delivery(measured, settings):
                break
            gain = max(-limit, min(limit, gain + settings.target_lufs - measured.integrated_lufs))
            if measured.true_peak_dbtp is not None:
                overshoot = measured.true_peak_dbtp - settings.true_peak_ceiling_dbtp
                ceiling -= max(0.0, overshoot)
        _log.info(
            "Mastering done",
            passes=passes,
            lufs=measured.integrated_lufs,
            true_peak=measured.true_peak_dbtp,
        )
        identity = self._engine.identity
        record = StageRecord(
            stage,
            StageStatus.APPLIED,
            f"{current.integrated_lufs:.1f} LUFS -> target {settings.target_lufs:.1f} LUFS",
            identity.name,
            identity.version,
            {
                "target_lufs": settings.target_lufs,
                "true_peak_ceiling_dbtp": settings.true_peak_ceiling_dbtp,
                "gain_db": gain,
                "passes": passes,
            },
        )
        return MasteringOutcome(output, record, measured)

    def _unchanged(
        self, source: Path, current: QualityMeasurements, reason: str
    ) -> MasteringOutcome:
        identity = self._engine.identity
        record = StageRecord(
            ProcessingStage.MASTERING, StageStatus.SKIPPED, reason, identity.name, identity.version
        )
        return MasteringOutcome(source, record, current)
