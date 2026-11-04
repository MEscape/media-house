"""Business errors of the visual style engine."""

from media_house.shared.errors import DomainError


class VisualStyleError(DomainError):
    code = "visual_style_engine.error"


class InvalidStyleConfig(VisualStyleError):
    """An unknown profile or token, a wrong value type, a missing reason or a locked token."""

    code = "visual_style_engine.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid visual style configuration: {detail}",
            user_message=f"The visual style settings are not valid: {detail}",
        )


class UnknownIdentityVersion(VisualStyleError):
    code = "visual_style_engine.unknown_identity_version"

    def __init__(self, version: int) -> None:
        super().__init__(
            f"Channel identity version {version} does not exist",
            user_message=f"There is no channel identity version {version}.",
            details={"version": version},
        )


class UnsupportedOutputFormat(VisualStyleError):
    """No layout is defined for the requested aspect ratio."""

    code = "visual_style_engine.unsupported_output"

    def __init__(self, width: int, height: int, known: str) -> None:
        super().__init__(
            f"No layout for a {width}x{height} output (known aspects: {known})",
            user_message=f"The channel style has no layout for {width}x{height}.",
            details={"width": width, "height": height},
        )


class InvalidStyle(VisualStyleError):
    """The resolved style failed its own critical checks. It is never stored."""

    code = "visual_style_engine.invalid_style"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Visual style rejected: " + "; ".join(problems[:3]),
            user_message="The visual style could not be built consistently.",
        )


class InvalidStyleDocument(VisualStyleError):
    """A stored style that cannot be read back (damaged, or from an unknown schema)."""

    code = "visual_style_engine.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored visual style unusable: {detail}")


class StyleRefMismatch(VisualStyleError):
    """A ``StyleRef`` whose fingerprint no longer matches the stored document."""

    code = "visual_style_engine.style_ref_mismatch"

    def __init__(self, document_id: str) -> None:
        super().__init__(
            f"Style document {document_id} does not match the referenced fingerprint",
            user_message="The referenced visual style has changed; resolve it again.",
            details={"document_id": document_id},
        )


class FontUnavailable(VisualStyleError):
    """A bundled font file is missing or its bytes differ from the recorded sha256."""

    code = "visual_style_engine.font_unavailable"

    def __init__(self, face: str, detail: str) -> None:
        super().__init__(
            f"Font {face} unavailable: {detail}",
            user_message=f"The font {face} is not available: {detail}",
            details={"face": face},
        )
