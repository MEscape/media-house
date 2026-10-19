"""Hugging Face model snapshots that are already on disk (never downloads).

Runs never fetch models: ``scripts/fetch_models.py`` is the one explicit download step. Every
module resolves a model name to a local folder through here, so "is it cached?" has one answer.
"""

from pathlib import Path


def cached_snapshot(repo: str, cache: Path | None = None) -> Path | None:
    """The local snapshot folder of ``repo`` when it is cached, else ``None``."""
    try:
        from huggingface_hub import snapshot_download

        return Path(
            snapshot_download(
                repo_id=repo,
                cache_dir=None if cache is None else str(cache),
                local_files_only=True,
            )
        )
    except Exception:  # noqa: BLE001  (huggingface_hub raises several unrelated types for "not cached")
        return None


def local_model_path(name: str, model_dir: Path | None = None) -> Path | None:
    """A folder path as given, else ``<model_dir>/<org>--<name>``, else the cached snapshot."""
    explicit = Path(name)
    if explicit.is_dir():
        return explicit
    slug = model_dir / name.replace("/", "--") if model_dir is not None else None
    if slug is not None and slug.is_dir():
        return slug
    return cached_snapshot(name)
