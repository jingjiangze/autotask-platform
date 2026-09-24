# -*- coding: utf-8 -*-
"""Local backend resolution for Local adapters.

IMPORTANT — `order_platform.py` is **side-effectful on import**
(verified 2026-09-22, order_platform.py:918-927):

    init_db()                     # writes the real orders/platform.db
    _ensure_settings()            # writes settings rows
    ensure_admin_password()       # writes users
    migrate_encrypt_passwords()   # may rewrite password rows
    migrate_timestamp_years()     # rewrites timestamps
    recover_stale_orders()        # mutates order states
    threading.Thread(order_watchdog)        # 4 long-lived background threads
    threading.Thread(concurrency_manager)
    threading.Thread(qr_janitor)
    threading.Thread(housekeeping)

Therefore Local adapters NEVER import it eagerly. They resolve it lazily and
refuse outright when CLOUD_TEST_MODE is enabled, so a cloud-test process can
never accidentally bind to the real platform. Tests always inject an explicit
backend object instead of triggering this import.
"""
from __future__ import annotations

import os

from cloud_test.adapters.base import AdapterContractError

__all__ = ["LocalBackendUnavailable", "resolve_backend"]


class LocalBackendUnavailable(AdapterContractError):
    """The real platform backend cannot be used in this environment."""


def resolve_backend():
    """Return the real order_platform module, or fail closed.

    Refuses when CLOUD_TEST_MODE=1 (a cloud-test runtime must never touch the
    production platform), and refuses to import if the caller supplied no
    explicit backend inside a pytest/CI process without opt-in.
    """
    if os.environ.get("CLOUD_TEST_MODE", "0") == "1":
        raise LocalBackendUnavailable(
            "local backend refused: CLOUD_TEST_MODE=1 must use synthetic adapters"
        )
    import order_platform  # noqa: F401  (intentionally deferred; see module docstring)

    return order_platform
