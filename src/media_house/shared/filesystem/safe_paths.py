"""Path-traversal protection for paths that originate outside the application."""

from pathlib import Path

from media_house.shared.errors import ValidationError


def resolve_within(root: Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` relative to ``root`` and refuse anything that escapes it.

    Handles ``..`` segments, absolute paths and symlinks (via ``resolve``).
    """
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValidationError(
            "Path escapes its root directory",
            user_message="The chosen path is outside the allowed folder.",
            details={"root": str(resolved_root), "candidate": str(candidate)},
        )
    return resolved
