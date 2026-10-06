"""Error taxonomy and Result type.

This package is a leaf: it imports nothing else from Media-House. The central
``ErrorHandler`` lives in ``media_house.shared.errors.handler`` (it needs logging).
"""

from media_house.shared.errors.base import (
    ApplicationError,
    ConfigurationError,
    ConflictError,
    DomainError,
    ErrorCategory,
    ExternalSystemError,
    InfrastructureError,
    InvariantViolation,
    MediaHouseError,
    NotFoundError,
    OperationCancelledError,
    PersistenceError,
    ProcessFailedError,
    ProcessTimeoutError,
    ToolNotFoundError,
    UnexpectedError,
    ValidationError,
    new_error_id,
)
from media_house.shared.errors.result import Err, Ok, Result

__all__ = [
    "ApplicationError",
    "ConfigurationError",
    "ConflictError",
    "DomainError",
    "Err",
    "ErrorCategory",
    "ExternalSystemError",
    "InfrastructureError",
    "InvariantViolation",
    "MediaHouseError",
    "NotFoundError",
    "Ok",
    "OperationCancelledError",
    "PersistenceError",
    "ProcessFailedError",
    "ProcessTimeoutError",
    "Result",
    "ToolNotFoundError",
    "UnexpectedError",
    "ValidationError",
    "new_error_id",
]
