"""``local_model_path``: the one place that decides where a model name lives on disk."""

from pathlib import Path

from media_house.core.infrastructure.models import local_model_path


def test_an_existing_folder_is_used_as_given(tmp_path: Path) -> None:
    assert local_model_path(str(tmp_path)) == tmp_path


def test_the_model_directory_slug_is_found(tmp_path: Path) -> None:
    folder = tmp_path / "org--name"
    folder.mkdir()

    assert local_model_path("org/name", tmp_path) == folder


def test_an_uncached_model_is_none_without_a_download(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    assert local_model_path("org/never-fetched-model", tmp_path) is None
