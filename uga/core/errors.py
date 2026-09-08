class UGAError(Exception):
    """Base class for domain errors that callers may handle."""


class ContractViolation(UGAError, ValueError):
    """Raised when data violates an architectural contract."""


class ClockRegressionError(ContractViolation):
    """Raised when the single monotonic timeline moves backwards."""


class BackendUnavailableError(UGAError, RuntimeError):
    """Raised when a requested platform backend cannot operate."""


class BackendStateError(UGAError, RuntimeError):
    """Raised when a backend lifecycle operation is invalid."""


class CaptureTimeoutError(UGAError, TimeoutError):
    """Raised when a capture backend has no frame before its bounded deadline."""


class CaptureAccessLostError(BackendUnavailableError):
    """Raised when a capture session must be recreated after a display transition."""


class LeaseDeniedError(UGAError, PermissionError):
    """Raised when a lower-priority owner cannot acquire control."""
