"""Business errors of the revision engine."""

from media_house.shared.errors import DomainError


class RevisionError(DomainError):
    code = "revision.error"


class InvalidRevisionDocument(RevisionError):
    code = "revision.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Stored revision plan is unreadable: {detail}",
            user_message="A stored revision plan could not be read.",
        )


class PlanNotCurrent(RevisionError):
    """The plan was made for another version of the timeline than the review holds."""

    code = "revision.plan_not_current"

    def __init__(self, plan_version: int, current_version: int) -> None:
        super().__init__(
            f"Plan made at timeline version {plan_version}, the timeline is at {current_version}",
            user_message=(
                f"That plan was made for version {plan_version} of the timeline; it is now at "
                f"version {current_version}. Ask for a fresh plan."
            ),
        )


class NothingToRevise(RevisionError):
    code = "revision.nothing_to_revise"

    def __init__(self, detail: str) -> None:
        super().__init__(detail, user_message=detail)


class NeedsClarification(RevisionError):
    """The words cannot be acted on without asking: the question travels with the error."""

    code = "revision.needs_clarification"

    def __init__(self, question: str) -> None:
        super().__init__(question, user_message=question)
        self.question = question


class ConflictWithPersonsWork(RevisionError):
    code = "revision.conflicts_with_manual_work"

    def __init__(self, what: str) -> None:
        super().__init__(
            f"Would overwrite a person's work: {what}",
            user_message=f"That would overwrite your own changes ({what}); nothing was changed.",
        )
