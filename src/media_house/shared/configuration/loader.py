"""Configuration loading.

``os.environ`` is read exactly once, in the CLI bootstrap, and passed in here
as a plain mapping. That keeps this module deterministic and trivially testable.
"""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from media_house.shared.configuration.settings import AppSettings, Environment
from media_house.shared.errors import ConfigurationError

ENV_PREFIX = "MEDIA_HOUSE_"
SECRET_PREFIX = "MEDIA_HOUSE_SECRET_"  # noqa: S105 - a prefix, not a credential
#: Variables consumed by the bootstrap itself, not part of AppSettings.
RESERVED_ENV_KEYS = frozenset({"HOME"})

#: Defaults that differ per environment (second-lowest precedence layer).
ENVIRONMENT_DEFAULTS: Mapping[Environment, Mapping[str, Any]] = {
    Environment.DEVELOPMENT: {"logging": {"level": "DEBUG", "console": True}},
    Environment.TEST: {"logging": {"level": "WARNING", "console": False, "file": False}},
    Environment.PRODUCTION: {"logging": {"level": "INFO", "console": False, "file": True}},
}

type RawConfig = dict[str, Any]


def load_settings(
    *,
    config_file: Path | None,
    env: Mapping[str, str],
    overrides: Mapping[str, Any] | None = None,
) -> AppSettings:
    """Build validated settings or raise :class:`ConfigurationError`."""
    file_layer = _read_toml(config_file) if config_file is not None else {}
    if "secrets" in file_layer:
        raise ConfigurationError(
            "'secrets' must not appear in the settings file",
            user_message="Secrets must be provided via environment variables, not settings.toml.",
            details={"file": str(config_file)},
        )
    explicit = _deep_merge(file_layer, _parse_env(env), dict(overrides or {}))
    environment = _resolve_environment(explicit.get("environment"))
    merged = _deep_merge(
        {"environment": environment.value},
        dict(ENVIRONMENT_DEFAULTS[environment]),
        explicit,
    )
    try:
        return AppSettings.model_validate(merged)
    except PydanticValidationError as exc:
        # include_input=False: never echo values (they may be secrets) into logs or UI.
        problems = [
            {"where": ".".join(map(str, e["loc"])), "problem": e["msg"]}
            for e in exc.errors(include_input=False, include_url=False)
        ]
        raise ConfigurationError(
            "Invalid configuration",
            user_message="The configuration contains invalid values: "
            + "; ".join(f"{p['where']}: {p['problem']}" for p in problems),
            details={"problems": problems, "file": str(config_file)},
        ) from exc


def _read_toml(path: Path) -> RawConfig:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(
            f"Cannot read settings file {path}",
            user_message=f"The settings file '{path.name}' could not be read or is not valid TOML.",
            details={"file": str(path)},
        ) from exc


def _parse_env(env: Mapping[str, str]) -> RawConfig:
    """``MEDIA_HOUSE_LOGGING__LEVEL=DEBUG`` -> ``{"logging": {"level": "DEBUG"}}``."""
    result: RawConfig = {}
    for key, value in env.items():
        if not key.startswith(ENV_PREFIX):
            continue
        if key.startswith(SECRET_PREFIX):
            name = key.removeprefix(SECRET_PREFIX).lower()
            result.setdefault("secrets", {})[name] = value
            continue
        name = key.removeprefix(ENV_PREFIX)
        if name in RESERVED_ENV_KEYS:
            continue
        *parents, leaf = name.lower().split("__")
        node = result
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return result


def _resolve_environment(raw: object) -> Environment:
    if raw is None:
        return Environment.PRODUCTION
    try:
        return Environment(str(raw).lower())
    except ValueError as exc:
        allowed = ", ".join(e.value for e in Environment)
        raise ConfigurationError(
            f"Unknown environment {raw!r}",
            user_message=f"Unknown environment '{raw}'. Allowed values: {allowed}.",
        ) from exc


def _deep_merge(*layers: Mapping[str, Any]) -> RawConfig:
    out: RawConfig = {}
    for layer in layers:
        for key, value in layer.items():
            if isinstance(value, Mapping) and isinstance(out.get(key), dict):
                out[key] = _deep_merge(out[key], value)
            elif isinstance(value, Mapping):
                out[key] = _deep_merge(value)
            else:
                out[key] = value
    return out
