"""Shared Normalization utilities."""

import re


def normalize_email(email_address: str | None) -> str | None:
    email = (email_address or "").strip().lower()
    if not email:
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None
    return email