"""Business errors of the VFX engine."""

from media_house.shared.errors import DomainError


class VfxEngineError(DomainError):
    code = "vfx_engine.error"


class InvalidVfxConfig(VfxEngineError):
    code = "vfx_engine.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid VFX configuration: {detail}",
            user_message=f"The effect settings are not valid: {detail}",
        )


class InvalidVfxShots(VfxEngineError):
    """The input shots contradict themselves (times, frame rate, sizes)."""

    code = "vfx_engine.invalid_shots"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid VFX input: {detail}",
            user_message=f"The shots handed to the effect stage are not valid: {detail}",
        )


class NothingToTreat(VfxEngineError):
    """No clip at all: there is nothing an effect could apply to."""

    code = "vfx_engine.nothing_to_treat"

    def __init__(self) -> None:
        super().__init__(
            "The VFX input has no clips",
            user_message="There are no shots to apply effects to.",
        )


class InvalidVfxPlan(VfxEngineError):
    """The planner produced a plan that breaks its own invariants (never stored)."""

    code = "vfx_engine.invalid_plan"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "The VFX plan is invalid: " + "; ".join(problems[:5]),
            user_message="The effect plan could not be made consistent and was not stored.",
            details={"problems": problems[:20]},
        )


class InvalidVfxDocument(VfxEngineError):
    code = "vfx_engine.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Unreadable VFX plan document: {detail}",
            user_message="A stored effect plan could not be read.",
        )
