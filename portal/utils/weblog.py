# -*- coding: utf-8 -*-
"""weblog.py — 脱敏日志（规范 §20 / §47）

任何日志都不允许出现 password / token / cookie / authorization / secret / api_key / session。
复用 order_platform.py 的 _scrub 正则思路（同一套脱敏规则，不另造一套）。
"""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

import config

_SENSITIVE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|apikey|secret|client[_-]?secret|"
    r"authorization|cookie|session|credential)\b\s*([=:]\s*|\s+)([^\s,;\"'&]+)")
_REDACTED = r"\1\2[REDACTED]"


def scrub(text):
    """把敏感值替换为 [REDACTED]，保留键名与分隔符，便于排查"""
    try:
        return _SENSITIVE.sub(_REDACTED, str(text))
    except Exception:
        return "[scrub-failed]"


class ScrubFilter(logging.Filter):
    def filter(self, record):
        try:
            record.msg = scrub(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


_logger = None


def logger():
    global _logger
    if _logger is not None:
        return _logger
    lg = logging.getLogger("portal")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(config.LOG_PATH, maxBytes=2 * 1024 * 1024,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(ScrubFilter())
    lg.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.addFilter(ScrubFilter())
    lg.addHandler(sh)

    _logger = lg
    return lg


def info(msg, *a):
    logger().info(scrub(msg % a if a else msg))


def warn(msg, *a):
    logger().warning(scrub(msg % a if a else msg))


def error(msg, *a):
    logger().error(scrub(msg % a if a else msg))
