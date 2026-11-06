"""Business errors of the audio mix engine."""

from media_house.shared.errors import DomainError


class AudioMixError(DomainError):
    code = "audio_mix_engine.error"


class InvalidMixConfig(AudioMixError):
    code = "audio_mix_engine.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid mix configuration: {detail}",
            user_message=f"The mix settings are not valid: {detail}",
        )


class NothingToMix(AudioMixError):
    """The timeline has no sound that can be mixed."""

    code = "audio_mix_engine.nothing_to_mix"

    def __init__(self, detail: str = "the timeline has no audio clips") -> None:
        super().__init__(
            f"Nothing to mix: {detail}", user_message=f"There is nothing to mix: {detail}"
        )


class TimelineNotMixable(AudioMixError):
    code = "audio_mix_engine.not_mixable"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Timeline not mixable: " + "; ".join(problems[:5]),
            user_message="The soundtrack cannot be mixed: " + "; ".join(problems[:3]),
            details={"problems": problems},
        )
        self.problems = tuple(problems)


class SpecificationInvalid(AudioMixError):
    """ERROR findings in the specification: nothing is rendered."""

    code = "audio_mix_engine.specification_invalid"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Mix specification invalid: " + "; ".join(problems[:5]),
            user_message="The mix specification has errors: " + "; ".join(problems[:3]),
            details={"problems": problems},
        )
        self.problems = tuple(problems)


class BackendFailed(AudioMixError):
    """The processing backend failed; nothing valid was lost (earlier outputs stay)."""

    code = "audio_mix_engine.backend_failed"

    def __init__(self, operation: str, detail: str) -> None:
        super().__init__(
            f"The audio backend failed to {operation}: {detail}",
            user_message=f"Audio processing failed ({operation}): {detail}",
            details={"operation": operation},
        )
        self.operation = operation


class NothingRendered(AudioMixError):
    code = "audio_mix_engine.nothing_rendered"

    def __init__(self) -> None:
        super().__init__(
            "No mix has been rendered for this timeline",
            user_message="No mix has been rendered for this timeline yet.",
        )


class InvalidMixDocument(AudioMixError):
    code = "audio_mix_engine.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid stored mix document: {detail}",
            user_message="A stored mix document cannot be read.",
        )
