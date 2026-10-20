"""Expected failures of proxy management: the caller can act on every one of them."""

from media_house.shared.errors import DomainError


class ProxyError(DomainError):
    """Base class for failures the user can act on."""

    code = "proxy_manager.failed"


class InvalidProfile(ProxyError):
    code = "proxy_manager.invalid_profile"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid proxy profile: {reason}",
            user_message=f"The proxy profile is not valid: {reason}",
        )


class UnsupportedSource(ProxyError):
    """Media this version cannot proxy faithfully; it says why instead of guessing."""

    code = "proxy_manager.unsupported_source"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Source not supported: {reason}",
            user_message=f"A proxy cannot be made from this media yet: {reason}",
        )


class ValidationFailed(ProxyError):
    """The generated file broke a promise of its profile; it was discarded."""

    code = "proxy_manager.validation_failed"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Proxy validation failed: {reason}",
            user_message="The proxy did not pass its checks and was discarded.",
        )


class Unverifiable(ProxyError):
    """The checks could not be completed, so the file is not published as a ready proxy."""

    code = "proxy_manager.unverifiable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Proxy cannot be verified: {reason}",
            user_message="The proxy could not be verified and was not published.",
        )


class OutputExceedsReservation(ProxyError):
    code = "proxy_manager.output_too_large"

    def __init__(self, produced: int, reserved: int) -> None:
        super().__init__(
            f"Output {produced} bytes exceeds the reservation of {reserved} bytes",
            user_message="The proxy turned out larger than allowed and was discarded.",
            details={"produced": produced, "reserved": reserved},
        )


class InsufficientSpace(ProxyError):
    """Not enough free disk for the proxy (transient: free space and try again)."""

    code = "proxy_manager.insufficient_space"

    def __init__(self, required: int, available: int) -> None:
        super().__init__(
            f"Need {required} bytes, {available} available",
            user_message="There is not enough free disk space to make this proxy.",
            details={"required": required, "available": available},
        )


class AttemptInFlight(ProxyError):
    """An identical generation is already running (waiting for it arrives in a later slice)."""

    code = "proxy_manager.in_flight"

    def __init__(self, attempt_id: str) -> None:
        super().__init__(
            f"Attempt {attempt_id} is already generating this proxy",
            user_message="This proxy is already being generated.",
            details={"attempt_id": attempt_id},
        )
        self.attempt_id = attempt_id


class Superseded(ProxyError):
    """A newer request for the same slot won; this result was kept but not activated."""

    code = "proxy_manager.superseded"

    def __init__(self, attempt_id: str) -> None:
        super().__init__(
            f"Attempt {attempt_id} was superseded by a newer request",
            user_message="A newer request for this proxy replaced this one.",
            details={"attempt_id": attempt_id},
        )


class ArtifactUnrepairable(ProxyError):
    """An unusable library asset owns this derivation and cannot safely be replaced."""

    code = "proxy_manager.unrepairable"

    def __init__(self, asset_id: str, reason: str) -> None:
        super().__init__(
            f"Existing proxy asset {asset_id} cannot be repaired: {reason}",
            user_message=f"An existing proxy is unusable and cannot be replaced: {reason}",
            details={"asset_id": asset_id},
        )


class ArtifactBeingRemoved(ProxyError):
    """The proxy is being deleted right now; asking again after it is gone starts afresh."""

    code = "proxy_manager.being_removed"

    def __init__(self, asset_id: str) -> None:
        super().__init__(
            f"Proxy asset {asset_id} is being removed",
            user_message="This proxy is being removed. Try again in a moment.",
            details={"asset_id": asset_id},
        )


class SharedAttemptFailed(ProxyError):
    """The identical request this one waited for did not produce a proxy."""

    code = "proxy_manager.shared_attempt_failed"

    def __init__(self, attempt_id: str, outcome: str, reason: str) -> None:
        super().__init__(
            f"Attempt {attempt_id} {outcome}: {reason}",
            user_message=f"The identical request this one waited for {outcome}.",
            details={"attempt_id": attempt_id, "outcome": outcome},
        )


class WaitTimedOut(ProxyError):
    """The identical request is still running after the longest time this call may wait."""

    code = "proxy_manager.wait_timed_out"

    def __init__(self, attempt_id: str, seconds: float) -> None:
        super().__init__(
            f"Attempt {attempt_id} still running after {seconds:.0f}s",
            user_message="An identical request is still running. Try again later.",
            details={"attempt_id": attempt_id},
        )


class LeaseLost(ProxyError):
    """This attempt was retired because its owner stopped responding for longer than the lease.

    Everything it still holds is abandoned: another request may already be doing the same work.
    """

    code = "proxy_manager.lease_lost"

    def __init__(self, attempt_id: str) -> None:
        super().__init__(
            f"Attempt {attempt_id} was retired after its lease expired",
            user_message="This request was taken over by another one and stopped. Try again.",
            details={"attempt_id": attempt_id},
        )
