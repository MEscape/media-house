# ADR-0004: Layered configuration, validated with pydantic

Status: accepted

**Context.** Configuration comes from defaults, environment, a user file and the command line, plus secrets. It must
fail early, never be scattered as `os.getenv`, and never reach the domain.

**Decision.** `shared/configuration`: typed pydantic models (`extra="forbid"`, frozen) and a small explicit loader
that merges *defaults → per-environment defaults → settings.toml → MEDIA_HOUSE_* env → CLI*. `os.environ` is read once
(CLI bootstrap) and passed in as a mapping. Secrets only from `MEDIA_HOUSE_SECRET_*`, stored as `SecretStr`;
a `[secrets]` table in the file is rejected. Validation errors name the field but never echo the value.

**Alternatives.** `pydantic-settings` (more magic around sources/precedence, extra dependency, harder to inject a
fake environment); `dynaconf`/`configparser` (weaker typing).

**Consequences.** + Deterministic, testable, strict. - ~100 lines of loader to maintain instead of a library.
