# -*- coding: utf-8 -*-
"""SyntheticAuthAdapter — in-memory identity store (F02/F03/F04).

No real accounts, no real database, no network. Sessions are an in-memory dict
(a Durable Object backs this in a later commit). Password hashing is a separate
synthetic scheme and is deliberately NOT compatible with the local platform
(ADAPTER_CONTRACT §5.2 / §56).
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Dict, Optional

from cloud_test.adapters.base import AdapterContractError, AuthError, SessionUser, ValidationError

#: Synthetic registration code (a placeholder, not a secret).
DEFAULT_REG_CODE = "SYN-REG-CODE"

#: Seeded synthetic users: username -> (password, is_admin). Marker-only values.
SEED_USERS = (
    ("synthetic-admin", "syn-pass-admin", True),
    ("synthetic-user-001", "syn-pass-001", False),
    ("synthetic-user-002", "syn-pass-002", False),
)
SYNTHETIC_SALT = "synthetic-salt"


class SyntheticAuthAdapter:
    name = "synthetic-auth"
    is_synthetic = True

    def __init__(self, reg_code: str = DEFAULT_REG_CODE, seed: bool = True):
        self._reg_code = reg_code
        self._users: Dict[str, dict] = {}
        self._sessions: Dict[str, int] = {}
        self._next_id = 1
        if seed:
            for username, password, is_admin in SEED_USERS:
                self._add(username, password, is_admin)

    # ------------------------------------------------------------------ utils
    def _add(self, username: str, password: str, is_admin: bool = False) -> SessionUser:
        user = {"id": self._next_id, "username": username,
                "pw_hash": self.hash_password(password), "is_admin": is_admin}
        self._users[username] = user
        self._next_id += 1
        return SessionUser(id=user["id"], username=username, is_admin=is_admin)

    def _by_id(self, user_id: int) -> Optional[dict]:
        for u in self._users.values():
            if u["id"] == user_id:
                return u
        return None

    @property
    def reg_code(self) -> str:
        return self._reg_code

    # -------------------------------------------------------------- interface
    def hash_password(self, password: str) -> str:
        return hashlib.sha256(f"{SYNTHETIC_SALT}:{password}".encode()).hexdigest()

    def register(self, reg_code: str, username: str, password: str) -> SessionUser:
        if not reg_code or reg_code != self._reg_code:
            raise ValidationError("注册口令不正确")
        if not username or not password:
            raise ValidationError("用户名和密码不能为空")
        if username in self._users:
            raise ValidationError("用户名已存在")
        return self._add(username, password, False)

    def login(self, username: str, password: str) -> SessionUser:
        if not username or not password:
            raise ValidationError("用户名和密码不能为空")
        user = self._users.get(username)
        if user is None or user["pw_hash"] != self.hash_password(password):
            raise AuthError("用户名或密码错误")
        return SessionUser(id=user["id"], username=username, is_admin=user["is_admin"])

    def create_session(self, user_id: int) -> str:
        """Mint a synthetic session token (mirrors the platform's cookie token)."""
        user = self._by_id(int(user_id))
        if user is None:
            raise AuthError("用户记录不存在")
        token = uuid.uuid4().hex
        self._sessions[token] = int(user_id)
        return token

    def session_user(self, session_token: str) -> Optional[SessionUser]:
        uid = self._sessions.get(session_token or "")
        if uid is None:
            return None
        user = self._by_id(uid)
        if user is None:
            raise AdapterContractError("session references a missing synthetic user")
        return SessionUser(id=user["id"], username=user["username"], is_admin=user["is_admin"])

    def logout(self, session_token: str) -> None:
        self._sessions.pop(session_token or "", None)

    @property
    def session_count(self) -> int:
        return len(self._sessions)
