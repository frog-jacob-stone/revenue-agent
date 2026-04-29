"""Chain definitions registered at import time."""
from app.orchestrator.chains import outreach, rev_rec


def register_all() -> None:
    """Register every built-in chain. Called from app startup."""
    outreach.register()
    rev_rec.register()


__all__ = ["outreach", "rev_rec", "register_all"]
