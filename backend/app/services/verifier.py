"""Server-side fall verifier interface.

STATUS: PENDING INTEGRATION. No trained model is deployed yet (see docs/AI_MODEL.md).
`UnavailableVerifier` returns None, which the confidence engine treats as "verifier
unavailable" and falls back to the device score — it never fabricates a score.
"""

from typing import Protocol

from app.services.evidence import Frame


class FallVerifier(Protocol):
    name: str

    def verify(self, frames: list[Frame]) -> float | None: ...


class UnavailableVerifier:
    name = "unavailable"

    def verify(self, frames: list[Frame]) -> float | None:
        return None


_verifier: FallVerifier = UnavailableVerifier()


def get_verifier() -> FallVerifier:
    return _verifier


def set_verifier(v: FallVerifier) -> None:
    global _verifier
    _verifier = v
