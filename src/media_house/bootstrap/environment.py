"""Startup environment validation: fail early, with an actionable message."""

import uuid

from media_house.shared.errors import InfrastructureError
from media_house.shared.filesystem import AppPaths


def validate_environment(paths: AppPaths) -> None:
    """Create the data directories and prove that we can write to them."""
    paths.ensure_directories()
    for directory in (paths.config_dir, paths.data_dir, paths.cache_dir, paths.log_dir):
        probe = directory / f".write-test-{uuid.uuid4().hex}"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise InfrastructureError(
                f"Directory is not writable: {directory}",
                user_message=(
                    "Media-House cannot write to one of its folders. "
                    "Check permissions and free disk space."
                ),
                details={"directory": str(directory)},
            ) from exc
