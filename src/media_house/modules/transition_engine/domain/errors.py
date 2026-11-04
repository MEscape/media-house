"""Business errors of the transition engine."""

from media_house.shared.errors import DomainError


class TransitionEngineError(DomainError):
    code = "transition_engine.error"


class InvalidTransitionConfig(TransitionEngineError):
    code = "transition_engine.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid transition configuration: {detail}",
            user_message=f"The transition settings are not valid: {detail}",
        )


class InvalidTransitionSequence(TransitionEngineError):
    """The input sequence is malformed (the caller's mapping is wrong)."""

    code = "transition_engine.invalid_sequence"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid transition sequence: {detail}",
            user_message=f"The edit given to the transition engine is not valid: {detail}",
        )


class NothingToTransition(TransitionEngineError):
    """The sequence has no clip on the main picture layer."""

    code = "transition_engine.nothing_to_transition"

    def __init__(self) -> None:
        super().__init__(
            "The sequence has no main picture clip",
            user_message="There is no clip to plan transitions for.",
        )


class InvalidTransitionPlan(TransitionEngineError):
    """The planner's own result failed a CRITICAL check. It is a bug, never stored."""

    code = "transition_engine.invalid_plan"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Transition plan rejected: " + "; ".join(problems[:3]),
            user_message="The transition plan could not be built consistently.",
        )


class InvalidTransitionDocument(TransitionEngineError):
    """A stored plan that cannot be read back (damaged, or from an unknown schema)."""

    code = "transition_engine.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored transition plan unusable: {detail}")
