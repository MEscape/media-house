from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from media_house.shared.configuration import AppSettings, Environment, LogLevel, load_settings
from media_house.shared.errors import ConfigurationError


def load(
    env: Mapping[str, str] | None = None,
    *,
    config_file: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> AppSettings:
    return load_settings(config_file=config_file, env=env or {}, overrides=overrides)


def test_defaults_are_production_safe() -> None:
    settings = load()
    assert settings.environment is Environment.PRODUCTION
    assert settings.logging.level is LogLevel.INFO
    assert settings.logging.console is False
    assert settings.logging.file is True


def test_development_environment_changes_defaults() -> None:
    settings = load({"MEDIA_HOUSE_ENVIRONMENT": "development"})
    assert settings.logging.level is LogLevel.DEBUG
    assert settings.logging.console is True


def test_test_environment_does_not_write_log_files() -> None:
    assert load({"MEDIA_HOUSE_ENVIRONMENT": "test"}).logging.file is False


def test_precedence_file_then_environment_then_overrides(tmp_path: Path) -> None:
    file = tmp_path / "settings.toml"
    file.write_text('[logging]\nlevel = "WARNING"\n[ui]\ntheme = "dark"\n', encoding="utf-8")

    from_file = load(config_file=file)
    assert from_file.logging.level is LogLevel.WARNING
    assert from_file.ui.theme.value == "dark"

    from_env = load({"MEDIA_HOUSE_LOGGING__LEVEL": "ERROR"}, config_file=file)
    assert from_env.logging.level is LogLevel.ERROR
    assert from_env.ui.theme.value == "dark"  # untouched layers survive

    from_cli = load(
        {"MEDIA_HOUSE_LOGGING__LEVEL": "ERROR"},
        config_file=file,
        overrides={"logging": {"level": "DEBUG"}},
    )
    assert from_cli.logging.level is LogLevel.DEBUG


def test_user_file_beats_environment_defaults(tmp_path: Path) -> None:
    file = tmp_path / "settings.toml"
    file.write_text('environment = "development"\n[logging]\nlevel = "ERROR"\n', encoding="utf-8")
    settings = load(config_file=file)
    assert settings.logging.level is LogLevel.ERROR
    assert settings.logging.console is True  # still from development defaults


def test_nested_environment_variables_are_coerced() -> None:
    assert load({"MEDIA_HOUSE_CONCURRENCY__MAX_WORKERS": "2"}).concurrency.max_workers == 2


def test_unrelated_and_reserved_variables_are_ignored() -> None:
    settings = load({"PATH": "/bin", "MEDIA_HOUSE_HOME": "/somewhere", "OTHER_LOGGING": "x"})
    assert settings.logging.level is LogLevel.INFO


def test_secrets_come_from_environment_and_never_print() -> None:
    settings = load({"MEDIA_HOUSE_SECRET_API_KEY": "hunter2"})
    assert settings.secrets["api_key"].get_secret_value() == "hunter2"
    assert "hunter2" not in repr(settings)
    assert "hunter2" not in str(settings.model_dump())


def test_secrets_in_the_settings_file_are_rejected(tmp_path: Path) -> None:
    file = tmp_path / "settings.toml"
    file.write_text('[secrets]\napi_key = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="secrets"):
        load(config_file=file)


def test_unknown_keys_fail_fast() -> None:
    with pytest.raises(ConfigurationError):
        load({"MEDIA_HOUSE_LOGGING__LEVLE": "DEBUG"})


def test_invalid_value_error_names_the_field_but_does_not_echo_the_value() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load({"MEDIA_HOUSE_CONCURRENCY__MAX_WORKERS": "hunter2"})
    assert "max_workers" in caught.value.user_message
    assert "hunter2" not in caught.value.user_message
    assert "hunter2" not in str(caught.value.details)


def test_invalid_toml_is_a_configuration_error(tmp_path: Path) -> None:
    file = tmp_path / "settings.toml"
    file.write_text("this is = = not toml", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load(config_file=file)


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="Unknown environment"):
        load({"MEDIA_HOUSE_ENVIRONMENT": "staging"})


def test_missing_file_is_fine(tmp_path: Path) -> None:
    assert load(config_file=tmp_path / "absent.toml").environment is Environment.PRODUCTION
