"""Chain definitions registered at import time."""
from app.orchestrator.chains import outreach


def register_all() -> None:
    """Register every built-in chain. Called from app startup."""
    outreach.register()


__all__ = ["outreach", "register_all"]
