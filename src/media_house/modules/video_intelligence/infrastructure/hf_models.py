"""Hugging Face model snapshots on disk: resolved offline unless a download was explicitly allowed.

``transformers`` is installed already (a WhisperX dependency); models come from the local snapshot
cache. A repository that is not cached makes the analyzer ``not_available`` with the command that
fetches it, unless ``allow_downloads`` is set. The snapshot's commit hash is the model identity
(part of every cache key that depends on the model).
"""

from pathlib import Path

from media_house.modules.video_intelligence.infrastructure.model_files import library_missing
from media_house.shared.errors import ExternalSystemError


def _resolve(repo: str, cache: Path | None, local_only: bool) -> Path:
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            repo_id=repo,
            cache_dir=None if cache is None else str(cache),
            local_files_only=local_only,
        )
    )


def cached_snapshot(repo: str, cache: Path | None) -> Path | None:
    """The local snapshot directory of ``repo`` if it is cached, else ``None``."""
    try:
        return _resolve(repo, cache, local_only=True)
    except Exception:  # noqa: BLE001  (huggingface_hub raises several unrelated types for "not cached")
        return None


def snapshot_reason(repo: str, license_name: str, cache: Path | None, allow: bool) -> str | None:
    """Why ``repo`` cannot be used now (``None`` when it is cached or may be downloaded)."""
    missing = library_missing("torch", "transformers", "huggingface_hub")
    if missing:
        return missing
    if allow or cached_snapshot(repo, cache) is not None:
        return None
    return (
        f"the model {repo} ({license_name}) is not cached; fetch it once with "
        f"`huggingface-cli download {repo}` or allow model downloads"
    )


def ensure_snapshot(repo: str, cache: Path | None, allow: bool) -> Path:
    """The snapshot directory, downloading it first only when allowed."""
    found = cached_snapshot(repo, cache)
    if found is not None:
        return found
    if not allow:
        raise ExternalSystemError(f"the model {repo} is not cached and downloads are not allowed")
    try:
        return _resolve(repo, cache, local_only=False)
    except Exception as exc:
        raise ExternalSystemError(f"could not download the model {repo}: {exc}") from exc


def revision_of(snapshot: Path) -> str:
    """The commit hash of a snapshot directory (its folder name)."""
    return snapshot.name
