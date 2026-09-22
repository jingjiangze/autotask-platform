# -*- coding: utf-8 -*-
"""AuthAdapter contract — covers F02 register / F03 login / F04 logout.

Declaration only. Local implementation wraps order_platform.py
(hash_pw:205, login_user:208, current_user:213, register:1721, logout:1792).
Cloud implementation uses CloudAuthRepository (+ Cloudflare Access as an outer
identity boundary, while the in-app user/role model is preserved).
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from cloud_test.adapters.base import AdapterSurface, SessionUser

__all__ = ["AuthAdapter"]


@runtime_checkable
class AuthAdapter(AdapterSurface, Protocol):
    """Identity + session capability."""

    def register(self, reg_code: str, username: str, password: str) -> SessionUser:
        """Create an account. Wrong reg_code or duplicate name -> ValidationError."""
        ...

    def login(self, username: str, password: str) -> SessionUser:
        """Authenticate. Failure -> AuthError (never a (False, msg) tuple)."""
        ...

    def session_user(self, session_token: str) -> Optional[SessionUser]:
        """Resolve a session. Missing/expired session returns None (not an error)."""
        ...

    def logout(self, session_token: str) -> None:
        """Invalidate a session. Idempotent."""
        ...

    def hash_password(self, password: str) -> str:
        """Password hashing is adapter-internal: local and cloud hashes are
        NOT interchangeable (local uses hmac(secret, salt+pw))."""
        ...
