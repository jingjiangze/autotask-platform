# -*- coding: utf-8 -*-
"""LocalAuthAdapter — thin wrapper over the existing platform auth primitives.

Wraps (never rewrites): hash_pw (order_platform.py:205), login_user (:208),
session cookie scheme ``<uid>:<hash_pw("u"+uid)>`` used by current_user (:213),
db (:64), get_setting (:503), DEFAULT_REG_CODE. Register is composed from those
primitives because the existing implementation is inlined in the Flask route
(register():1721) — recorded as gap G2 in docs/LOCAL_FUNCTIONAL_PARITY.md.
"""
from __future__ import annotations

from typing import Optional

from cloud_test.adapters.base import AuthError, SessionUser, ValidationError
from cloud_test.adapters.local.backend import resolve_backend

__all__ = ["LocalAuthAdapter"]


class LocalAuthAdapter:
    """Auth capability backed by the real Windows platform (production only)."""

    name = "local-auth"
    is_synthetic = False

    def __init__(self, backend=None):
        self._backend = backend  # injected in tests; lazily resolved otherwise

    # ------------------------------------------------------------------ utils
    @property
    def backend(self):
        if self._backend is not None:
            return self._backend
        return resolve_backend()

    @staticmethod
    def _user(row) -> Optional[SessionUser]:
        if not row:
            return None
        keys = row.keys() if hasattr(row, "keys") else []
        is_admin = bool(row["is_admin"]) if "is_admin" in keys else False
        return SessionUser(id=int(row["id"]), username=row["username"], is_admin=is_admin)

    @property
    def reg_code(self) -> str:
        """Current registration code (same source as the register route)."""
        b = self.backend
        return b.get_setting("reg_code", b.DEFAULT_REG_CODE)

    # -------------------------------------------------------------- interface
    def hash_password(self, password: str) -> str:
        return self.backend.hash_pw(password)

    def register(self, reg_code: str, username: str, password: str) -> SessionUser:
        b = self.backend
        if not reg_code or reg_code != b.get_setting("reg_code", b.DEFAULT_REG_CODE):
            raise ValidationError("注册口令不正确")
        if not username or not password:
            raise ValidationError("用户名和密码不能为空")
        with b.db() as c:
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ValidationError("用户名已存在")
            c.execute("INSERT INTO users(username,pw_hash) VALUES(?,?)",
                      (username, b.hash_pw(password)))
            row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        user = self._user(row)
        if user is None:
            raise AuthError("注册后无法读取用户记录")
        return user

    def login(self, username: str, password: str) -> SessionUser:
        b = self.backend
        if not username or not password:
            raise ValidationError("用户名和密码不能为空")
        if not b.login_user(username, password):
            raise AuthError("用户名或密码错误")
        with b.db() as c:
            row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        user = self._user(row)
        if user is None:
            raise AuthError("用户记录不存在")
        return user

    def session_token(self, user_id: int) -> str:
        """Mint the platform's cookie token for a user id (no IO)."""
        return f"{int(user_id)}:{self.backend.hash_pw('u' + str(int(user_id)))}"

    def session_user(self, session_token: str) -> Optional[SessionUser]:
        if not session_token or ":" not in session_token:
            return None
        uid, sig = session_token.split(":", 1)
        if sig != self.backend.hash_pw("u" + uid):
            return None
        with self.backend.db() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return self._user(row)

    def logout(self, session_token: str) -> None:
        # The platform keeps no server-side session table: dropping the cookie
        # is the logout (order_platform.py:1792). Idempotent by construction.
        return None
