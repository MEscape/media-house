"""Model output, validated: JSON read strictly into a schema, repaired by asking again.

A model never decides anything free-form: its answer must parse into the typed schema, or it is
asked again with the exact error, up to ``attempts`` times; then the caller falls back to the
deterministic path. A confidential project never sends text to a remote model without consent.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from media_house.core.domain import json_codec
from media_house.modules.content_intelligence.application.contracts import TextGenerator
from media_house.modules.production_project.domain.project import ProjectSettings
from media_house.modules.production_project.domain.prompts import extract_json, schema_hint
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ConflictError, Err, ExternalSystemError, Ok, Result
from media_house.shared.logging import get_logger

log = get_logger(__name__)
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


@dataclass(frozen=True, slots=True)
class ModelUnusable:
    """The model could not be used or answered nothing valid (the caller falls back)."""

    reason: str


def is_remote(identity: str) -> bool:
    """A hosted model carries its host in its identity (``...@api.example.test``)."""
    host = identity.rpartition("@")[2] if "@" in identity else ""
    return bool(host) and host not in _LOOPBACK


def check_allowed(
    settings: ProjectSettings, generator: TextGenerator
) -> Result[None, ConflictError | ModelUnusable]:
    """Why this generator may not be used for this project, or ``Ok``. Local models are always
    fine; a remote one needs the consent a confidential project does not give by default."""
    why = generator.unavailable()
    if why:
        return Err(ModelUnusable(why))
    if settings.confidential and is_remote(generator.identity()) and not settings.remote_ai_allowed:
        return Err(
            ConflictError(
                "remote model refused for a confidential project",
                user_message=(
                    "Dieses Projekt ist vertraulich: Texte gehen nur an ein lokales Modell, "
                    "solange du ein externes nicht ausdrücklich erlaubst."
                ),
            )
        )
    return Ok(None)


def generate_structured[T](
    generator: TextGenerator,
    system: str,
    user: str,
    schema: type[T],
    cancellation: CancellationToken,
    *,
    attempts: int = 3,
    validate: Callable[[T], str] | None = None,
) -> Result[T, ModelUnusable]:
    """Ask, parse strictly, and on a bad answer ask again with the error."""
    prompt = user
    last = "keine Antwort"
    for attempt in range(1, attempts + 1):
        cancellation.raise_if_cancelled()
        try:
            answer = generator.generate(system, prompt, cancellation)
        except ExternalSystemError as exc:
            return Err(ModelUnusable(exc.user_message))
        try:
            decoded = json_codec.decode(schema, json.loads(extract_json(answer)))
            if validate is not None and (problem := validate(decoded)):
                raise ValueError(problem)
            return Ok(decoded)
        except (ValueError, TypeError) as exc:
            last = str(exc)
            log.info("Model answer rejected", attempt=attempt, reason=last[:200])
            prompt = (
                f"{user}\n\nDeine letzte Antwort war ungültig ({last}). Antworte erneut NUR mit "
                f"gültigem JSON in genau dieser Form:\n{schema_hint(schema)}"
            )
    return Err(
        ModelUnusable(f"Die Antwort des Modells war nach {attempts} Versuchen ungültig: {last}")
    )
