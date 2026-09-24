"""§72 runtime/environment —— 引擎子进程环境装配（WK_* 契约，plan §37）。

保持与现有 run_chaoxing/run_zhs 兼容的环境变量契约；凭据只进入
subprocess env，任务结束随目录清理。
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def credential_pairs(credentials: list[dict[str, str]]) -> dict[str, str]:
    """把 bootstrap 返回的凭据列表转成 {name: plaintext}。

    契约与 runners 一致：credential_type == "account" → 账号，
    "account_password" → 密码；其余类型按原名保留。
    """
    out: dict[str, str] = {}
    for c in credentials or []:
        ctype = c.get("credential_type", "")
        plain = c.get("plaintext")
        if plain is None:
            continue
        if ctype == "account":
            out["account"] = plain
        elif ctype == "account_password":
            out["password"] = plain
        else:
            out[ctype] = plain
    return out


def build_engine_env(credentials: list[dict[str, str]], extra: dict[str, Any] | None = None) -> dict[str, str]:
    """组装 WK_* 引擎环境（§37 兼容契约）。extra 里的值会被 str() 化。"""
    pairs = credential_pairs(credentials)
    env = {
        "WK_ACCOUNT": pairs.get("account", ""),
        "WK_PASSWORD": pairs.get("password", ""),
        "WK_UA": os.environ.get("WK_UA", DEFAULT_UA),
        "WK_PLATFORM": os.environ.get("WK_PLATFORM", "chaoxing"),
        "WK_LANG": os.environ.get("WK_LANG", "zh-CN"),
    }
    for k, v in (extra or {}).items():
        env[str(k)] = str(v)
    return env
