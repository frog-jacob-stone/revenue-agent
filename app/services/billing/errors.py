"""Shared billing exception base.

Lives on its own so `draws` and `groups` can share a base class without an
import cycle — group CRUD saves draws, so `groups` imports `draws`, and the
dependency cannot run the other way.

The router maps `BillingConfigError` to 400: every subclass means "the config
you sent is not valid", which is the caller's to fix.
"""
from __future__ import annotations


class BillingConfigError(Exception):
    """Invalid billing configuration. Surfaces as a 400."""
