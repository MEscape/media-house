"""Business errors of music and sound design."""

from media_house.shared.errors import DomainError


class SoundDesignError(DomainError):
    code = "music_and_sound_design.error"


class InvalidSoundConfig(SoundDesignError):
    code = "music_and_sound_design.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid sound design configuration: {detail}",
            user_message=f"The sound design settings are not valid: {detail}",
        )


class InvalidManifest(SoundDesignError):
    """The sound library manifest cannot be read or does not validate."""

    code = "music_and_sound_design.invalid_manifest"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Sound library manifest unusable: {detail}",
            user_message=f"The sound library manifest could not be read: {detail}",
        )


class UnreadableSound(SoundDesignError):
    """FFmpeg could not decode or measure a sound file."""

    code = "music_and_sound_design.unreadable_sound"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Sound file unreadable: {detail}",
            user_message="A sound file could not be read for analysis.",
        )


class NotASound(SoundDesignError):
    """The asset is not an imported library sound (no manifest facts in its metadata)."""

    code = "music_and_sound_design.not_a_sound"

    def __init__(self, asset_id: str) -> None:
        super().__init__(
            f"Asset {asset_id} is not a sound library asset",
            user_message="This file is not part of the sound library.",
            details={"asset_id": asset_id},
        )


class NothingToScore(SoundDesignError):
    """The timeline has no clip, so there is nothing to design sound for."""

    code = "music_and_sound_design.nothing_to_score"

    def __init__(self) -> None:
        super().__init__(
            "The soundtrack timeline has no clip",
            user_message="There is nothing to design sound for.",
        )


class InvalidSoundDesignPlan(SoundDesignError):
    """The planner's own result failed a CRITICAL check. It is a bug, never stored."""

    code = "music_and_sound_design.invalid_plan"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Sound design plan rejected: " + "; ".join(problems[:3]),
            user_message="The sound design plan could not be built consistently.",
        )


class InvalidSoundDocument(SoundDesignError):
    """A stored document that cannot be read back (damaged, or from an unknown schema)."""

    code = "music_and_sound_design.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored sound document unusable: {detail}")


class StemRenderFailed(SoundDesignError):
    code = "music_and_sound_design.stem_render_failed"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Sound stem render failed: {detail}",
            user_message="The music and effects stem could not be rendered.",
        )
