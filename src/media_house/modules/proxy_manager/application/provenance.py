"""How a proxy describes itself in its library asset's metadata.

The record travels with the asset, so any module can learn how a proxy was made without calling
this one (``read_proxy_provenance``). It is written once, at registration; the live verdict and
the slot assignment are the module's own records.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from media_house.modules.proxy_manager.domain.validation import ValidationReport
from media_house.modules.proxy_manager.domain.values import (
    METADATA_KEY,
    PROXY_OPERATION,
    Evidence,
    JsonValue,
    Verdict,
)

_SCHEMA = 1
_MAX_CHECK_DETAIL = 200


@dataclass(frozen=True, slots=True)
class ProxyProvenance:
    source_asset_id: str
    profile: str
    profile_version: int
    spec_fingerprint: str
    #: The ResolvedSpec as the library stored it (its derivation config).
    spec: Mapping[str, JsonValue]
    verdict: Verdict
    evidence: Evidence
    validator_version: int
    transformations: tuple[str, ...]
    encoder: str
    toolchain: str
    #: ``inspection`` or ``probe``: where the source facts came from.
    facts_origin: str
    attempt_id: str
    warnings: tuple[str, ...] = ()

    def to_metadata(self, report: ValidationReport) -> dict[str, JsonValue]:
        record: dict[str, JsonValue] = {
            "schema": _SCHEMA,
            "source_asset_id": self.source_asset_id,
            "profile": self.profile,
            "profile_version": self.profile_version,
            "spec_fingerprint": self.spec_fingerprint,
            "spec": dict(self.spec),
            "verdict": self.verdict.value,
            "evidence": self.evidence.value,
            "validator_version": self.validator_version,
            "transformations": list(self.transformations),
            "encoder": self.encoder,
            "toolchain": self.toolchain,
            "facts_origin": self.facts_origin,
            "attempt_id": self.attempt_id,
            "warnings": list(self.warnings),
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail[:_MAX_CHECK_DETAIL]}
                for c in report.checks
            ],
        }
        return {
            "processing_type": PROXY_OPERATION,
            "source_asset_id": self.source_asset_id,
            METADATA_KEY: record,
        }

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, JsonValue]) -> "ProxyProvenance | None":
        """The record, or ``None`` when the metadata carries no valid one (unknown, not 'none')."""
        record = metadata.get(METADATA_KEY)
        if not isinstance(record, dict) or record.get("schema") != _SCHEMA:
            return None
        try:
            spec = record["spec"]
            transformations = record["transformations"]
            warnings = record.get("warnings")
            if not isinstance(spec, dict) or not isinstance(transformations, list):
                return None
            return cls(
                source_asset_id=str(record["source_asset_id"]),
                profile=str(record["profile"]),
                profile_version=int(str(record["profile_version"])),
                spec_fingerprint=str(record["spec_fingerprint"]),
                spec=spec,
                verdict=Verdict(str(record["verdict"])),
                evidence=Evidence(str(record["evidence"])),
                validator_version=int(str(record["validator_version"])),
                transformations=tuple(str(t) for t in transformations),
                encoder=str(record["encoder"]),
                toolchain=str(record["toolchain"]),
                facts_origin=str(record["facts_origin"]),
                attempt_id=str(record["attempt_id"]),
                warnings=tuple(str(w) for w in warnings) if isinstance(warnings, list) else (),
            )
        except (KeyError, ValueError):
            return None
