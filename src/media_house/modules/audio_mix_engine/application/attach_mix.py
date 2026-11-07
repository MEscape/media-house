"""Attach the final mix to a rendered picture: the file a person reviews carries the mixed sound.

A picture rendered by an editing application carries the raw sound of the clips it placed (it
cannot apply the mix: no gain automation, no loudness correction). The mix engine's final mix
spans the same timeline, so the delivered file is that picture (copied, never re-encoded) with that
sound. Nothing is guessed: if the two do not have the same length (more than a few frames) the
sound would drift, so nothing is attached. The result is a derived asset of the picture and is
measured again, so the loudness of what is delivered is a measurement, not a promise.
"""

from dataclasses import dataclass

from media_house.modules.audio_mix_engine.application.ports import AudioBackend
from media_house.modules.audio_mix_engine.domain.errors import BackendFailed
from media_house.modules.audio_mix_engine.domain.result import Measurement
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths

ATTACH_OPERATION = "delivery_with_mix"
ATTACH_VERSION = 1
#: Picture and mix may differ by this much (a few frames of rounding), not more.
MAX_LENGTH_DIFFERENCE_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class AttachMixCommand:
    picture_asset_id: str
    mix_asset_id: str


@dataclass(frozen=True, slots=True)
class AttachMixResult:
    asset: MediaAssetDto
    #: What the delivered file measures (its sound), not what the mix was meant to be.
    measurement: Measurement
    created: bool


class AttachMix:
    def __init__(self, library: MediaLibrary, backend: AudioBackend, paths: AppPaths) -> None:
        self._library = library
        self._backend = backend
        self._paths = paths

    def execute(
        self, command: AttachMixCommand, ctx: JobContext
    ) -> Result[AttachMixResult, Exception]:
        picture = self._library.get(command.picture_asset_id)
        if isinstance(picture, Err):
            return picture
        picture_path = self._library.local_path(command.picture_asset_id)
        if isinstance(picture_path, Err):
            return picture_path
        mix_path = self._library.local_path(command.mix_asset_id)
        if isinstance(mix_path, Err):
            return mix_path
        cancellation = ctx.cancellation
        try:
            picture_sound = self._backend.measure(picture_path.value, cancellation)
            mix_sound = self._backend.measure(mix_path.value, cancellation)
            gap = abs(
                picture_sound.duration_samples / picture_sound.sample_rate
                - mix_sound.duration_samples / mix_sound.sample_rate
            )
            if gap > MAX_LENGTH_DIFFERENCE_SECONDS:
                raise BackendFailed(
                    "attach the mix",
                    f"the picture and the mix differ by {gap:.2f} s; the sound would drift",
                )
            with self._paths.temporary_directory(prefix="audio-attach-") as tmp:
                destination = tmp / "delivery.mp4"
                self._backend.attach_audio(
                    picture_path.value, mix_path.value, destination, cancellation
                )
                measurement = self._backend.measure(destination, cancellation)
                registered = self._library.register_derived(
                    command.picture_asset_id,
                    destination,
                    operation=ATTACH_OPERATION,
                    config={"mix_asset_id": command.mix_asset_id},
                    version=ATTACH_VERSION,
                    display_name=f"{picture.value.display_name} (with the mix)",
                    metadata={"mix_asset_id": command.mix_asset_id},
                )
                if isinstance(registered, Err):
                    return registered
        except BackendFailed as exc:
            return Err(exc)
        return Ok(AttachMixResult(registered.value.asset, measurement, registered.value.created))
