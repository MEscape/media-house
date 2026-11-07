"""Business errors of the final export."""

from media_house.shared.errors import DomainError


class ResolveExportError(DomainError):
    code = "resolve_export.error"


class InvalidExportDocument(ResolveExportError):
    code = "resolve_export.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Stored export report is unreadable: {detail}",
            user_message="A stored export report could not be read.",
        )


class NotReadyForExport(ResolveExportError):
    """Something that can never be overridden stands in the way of the export."""

    code = "resolve_export.not_ready"

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(
            "The export cannot start: " + "; ".join(reasons[:5]),
            user_message="The final export cannot start yet: " + "; ".join(reasons[:3]),
            details={"reasons": reasons},
        )
        self.reasons = tuple(reasons)


class ExportRefused(ResolveExportError):
    code = "resolve_export.refused"

    def __init__(self, reason: str) -> None:
        super().__init__(reason, user_message=reason)


class DeliveryRefused(ResolveExportError):
    code = "resolve_export.delivery_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(reason, user_message=reason)
