"""Business errors of the final polish."""

from media_house.shared.errors import DomainError


class FinalPolishError(DomainError):
    code = "final_polish.error"


class InvalidPolishDocument(FinalPolishError):
    code = "final_polish.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Stored final polish report is unreadable: {detail}",
            user_message="A stored final polish report could not be read.",
        )


class NotPolishable(FinalPolishError):
    code = "final_polish.not_polishable"

    def __init__(self, reason: str) -> None:
        super().__init__(reason, user_message=reason)
