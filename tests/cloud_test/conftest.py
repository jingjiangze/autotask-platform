# -*- coding: utf-8 -*-
"""Hard guard for the whole cloud_test suite.

Importing order_platform.py is side-effectful (order_platform.py:918-927:
init_db + settings/users migration + recover_stale_orders + 4 background
threads, all against the REAL orders/platform.db). No test here may ever
trigger that import — Local adapters must receive an injected fake backend.
"""
import sys

import pytest

SIDE_EFFECTFUL = "order_platform"


@pytest.fixture(autouse=True)
def forbid_side_effectful_platform_import():
    assert SIDE_EFFECTFUL not in sys.modules, (
        f"{SIDE_EFFECTFUL} was already imported; the suite must never touch the real platform"
    )
    yield
    assert SIDE_EFFECTFUL not in sys.modules, (
        f"{SIDE_EFFECTFUL} got imported during this test — it would have written the real "
        "orders/platform.db and started background threads. Inject a fake backend instead."
    )
