"""Chain definitions registered at import time."""
from app.orchestrator.chains import content, outreach, rev_rec


def register_all() -> None:
    """Register every built-in chain. Called from app startup."""
    outreach.register()
    rev_rec.register()
    content.register()


__all__ = ["content", "outreach", "rev_rec", "register_all"]
