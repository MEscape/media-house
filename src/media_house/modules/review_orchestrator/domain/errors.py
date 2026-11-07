"""Business errors of the review workflow. Each one names what was refused and why."""

from media_house.shared.errors import DomainError


class ReviewError(DomainError):
    code = "review.error"


class InvalidTransition(ReviewError):
    code = "review.invalid_transition"

    def __init__(self, source: str, target: str, reason: str = "") -> None:
        super().__init__(
            f"A review in state {source} cannot move to {target}"
            + (f": {reason}" if reason else ""),
            user_message=f"The review cannot go from '{source}' to '{target}' now."
            + (f" {reason}" if reason else ""),
            details={"from": source, "to": target},
        )


class StaleTimeline(ReviewError):
    """A decision or report refers to another version of the timeline than the review holds."""

    code = "review.stale_timeline"

    def __init__(self, session_version: int, given_version: int, what: str) -> None:
        super().__init__(
            f"{what} refers to timeline version {given_version}, "
            f"the review is at {session_version}",
            user_message=(
                f"That applies to an earlier version of the timeline (version {given_version}); "
                f"the review is now at version {session_version}. Look at the current one first."
            ),
            details={"session_version": session_version, "given_version": given_version},
        )


class NotPermitted(ReviewError):
    """The actor may not take this decision (a person is required, or the grant is missing)."""

    code = "review.not_permitted"

    def __init__(self, what: str, reason: str) -> None:
        super().__init__(
            f"{what} is not permitted: {reason}",
            user_message=f"{what} needs a person's decision: {reason}",
        )


class LockedSection(ReviewError):
    code = "review.section_locked"

    def __init__(self, section_id: str, what: str) -> None:
        super().__init__(
            f"{what} would change locked section {section_id}",
            user_message=f"Section '{section_id}' is locked, so {what} was not done.",
            details={"section_id": section_id},
        )


class NotAuthorized(ReviewError):
    """The final export has no valid authorization for exactly this timeline and output."""

    code = "review.export_not_authorized"

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(
            "Export is not authorized: " + "; ".join(reasons),
            user_message="The final export is not authorized: " + "; ".join(reasons[:3]),
            details={"reasons": reasons},
        )
        self.reasons = tuple(reasons)


class UnknownSubject(ReviewError):
    code = "review.unknown_subject"

    def __init__(self, kind: str, ident: str) -> None:
        super().__init__(
            f"Unknown {kind}: {ident}", user_message=f"There is no {kind} '{ident}' in this review."
        )


class InvalidReviewDocument(ReviewError):
    code = "review.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Stored review is unreadable: {detail}",
            user_message="A stored review could not be read.",
        )
