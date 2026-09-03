"""Stable domain failures for the authentication gateway."""


class AuthRejected(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class GatewayUnavailable(RuntimeError):
    """Raised when a server dependency cannot complete authentication."""


__all__ = ["AuthRejected", "GatewayUnavailable"]

