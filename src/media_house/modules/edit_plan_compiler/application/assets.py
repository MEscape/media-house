"""What the Media Library and the stored inspection say about the files a timeline points at.

Nothing is probed: a recording without a stored inspection is reported as not inspected and the
facts that need one (frame rate, variable rate) stay unknown. Derivatives (proxy, improved
audio / video) are listed so the compiler can trace them and, when the config asks, select them.
"""

from collections.abc import Iterable, Mapping
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.facts import AssetFacts, DerivativeFact
from media_house.modules.edit_plan_compiler.domain.model import ColorTag
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.shared.errors import Err

#: ``processing_type`` of the derived assets a clip may be pointed at instead of its recording.
DERIVATIVE_OPERATIONS = frozenset({"audio_improvement", "video_improvement", "proxy"})


def _seconds(value: float | None) -> Rational | None:
    """A probe's decimal seconds as the exact decimal they were printed as (not the binary)."""
    if value is None or value <= 0:
        return None
    exact = Fraction(repr(value))
    return Rational(exact.numerator, exact.denominator)


class AssetFactsReader:
    def __init__(self, library: MediaLibrary, inspections: InspectionCatalog) -> None:
        self._library = library
        self._inspections = inspections

    def read(
        self, ids: Iterable[str], *, with_derivatives: bool = False
    ) -> Mapping[str, AssetFacts]:
        found: dict[str, AssetFacts] = {}
        pending = sorted({i for i in ids if i})
        while pending:
            asset_id = pending.pop(0)
            if asset_id in found:
                continue
            facts = self._one(asset_id)
            found[asset_id] = facts
            if with_derivatives:
                pending += [
                    d.asset_id
                    for d in facts.derivatives
                    if d.asset_id not in found and facts.original_asset_id is None
                ]
        return found

    def _one(self, asset_id: str) -> AssetFacts:
        got = self._library.get(asset_id)
        if isinstance(got, Err):
            return AssetFacts(asset_id=asset_id, exists=False)
        dto = got.value
        path = self._library.local_path(asset_id)
        available = not isinstance(path, Err) and path.value.is_file()
        media = dto.media_type.value
        tech = dto.technical
        audio_streams = tech.get("audio_stream_count")
        has_audio = (
            audio_streams > 0
            if isinstance(audio_streams, int) and not isinstance(audio_streams, bool)
            else media == "audio"
        )
        rate = tech.get("sample_rate")
        channels = tech.get("channels")
        facts = AssetFacts(
            asset_id=asset_id,
            exists=True,
            file_available=available,
            media_type=media,
            original_asset_id=dto.derivation.source_asset_id if dto.derivation else None,
            operation=dto.derivation.operation if dto.derivation else None,
            checksum=dto.checksum,
            duration=_seconds(dto.duration_seconds),
            has_video=media == "video",
            has_audio=has_audio,
            sample_rate=rate if isinstance(rate, int) and not isinstance(rate, bool) else None,
            channels=channels
            if isinstance(channels, int) and not isinstance(channels, bool)
            else None,
            width=dto.width,
            height=dto.height,
            derivatives=self._derivatives(asset_id) if dto.derivation is None else (),
        )
        return self._inspected(facts)

    def _inspected(self, facts: AssetFacts) -> AssetFacts:
        found = self._inspections.find(facts.asset_id)
        if found is None or not found.inspection.status.usable:
            return facts
        media = found.inspection
        video = media.primary_video
        audio = media.audio_stream()
        rate = None
        variable = False
        if video is not None:
            rate = video.frame_rate.value or video.average_frame_rate.value
            variable = video.timing is not None and video.timing.mode.value == "variable"
        duration = media.container.duration
        return AssetFacts(
            asset_id=facts.asset_id,
            exists=True,
            file_available=facts.file_available,
            media_type=facts.media_type,
            original_asset_id=facts.original_asset_id,
            operation=facts.operation,
            checksum=facts.checksum,
            duration=_seconds(duration) or facts.duration,
            frame_rate=rate,
            frame_rate_variable=variable,
            has_video=video is not None,
            has_audio=audio is not None,
            sample_rate=audio.sample_rate if audio is not None else facts.sample_rate,
            channels=audio.channels if audio is not None else facts.channels,
            width=facts.width,
            height=facts.height,
            derivatives=facts.derivatives,
            inspected=True,
            color=None
            if video is None
            else ColorTag(
                space=video.color.space.value,
                primaries=video.color.primaries.value,
                transfer=video.color.transfer.value,
                range=video.color.range.value,
            ),
        )

    def _derivatives(self, asset_id: str) -> tuple[DerivativeFact, ...]:
        found: list[MediaAssetDto] = [
            d
            for d in self._library.list_derived(asset_id)
            if d.metadata.get("processing_type") in DERIVATIVE_OPERATIONS
        ]
        return tuple(
            DerivativeFact(
                asset_id=d.id,
                operation=str(d.metadata.get("processing_type")),
                duration=_seconds(d.duration_seconds),
            )
            for d in sorted(found, key=lambda d: (d.created_at, d.id), reverse=True)
        )
