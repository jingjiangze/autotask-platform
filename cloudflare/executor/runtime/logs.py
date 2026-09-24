"""§72 runtime/logs —— 日志脱敏（plan §68 SecretScrubber）。

RollingLog._scrub 的统一升级版：识别常见密钥形 key=value 与
WK_PASSWORD 等环境变量名，输出前必须过 scrub。
"""

from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"(?i)(password\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(passwd\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(pwd\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(token\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(api_key\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(apikey\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(authorization\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(cookie\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(set-cookie\s*[=:]\s*)\S+"),
    re.compile(r"(WK_PASSWORD|WK_ACCOUNT)\s*=\s*\S+"),
]


def scrub(text: str, redact: str = "***") -> str:
    out = text
    for pat in _PATTERNS:
        out = pat.sub(lambda m: f"{m.group(1)}{redact}", out)
    return out
