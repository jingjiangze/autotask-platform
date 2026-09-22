# -*- coding: utf-8 -*-
"""Minimal local smoke test: Runtime layer -> Identity connection.

Proves that with CLOUD_TEST_MODE=1 + CLOUD_TEST_EGRESS_REQUIRED=1 and a
verified (mocked) egress guard, assert_cloud_test_runtime() yields an
immutable RuntimeIdentity. Pure stdlib, no network, no nftables changes.

Usage (from repo root):
    python tools/smoke_cloud_test_runtime.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["CLOUD_TEST_MODE"] = "1"
os.environ["CLOUD_TEST_EGRESS_REQUIRED"] = "1"
os.environ["CLOUD_TEST_RUNNER_ID"] = "smoke-runner-01"
os.environ["CLOUD_TEST_GIT_SHA"] = "0" * 40

# Mock the real-kernel guard as verified: Commit 04 already proved the real
# nftables path on a hosted Linux runner; this smoke test only checks the
# Runtime -> Identity wiring.
import cloud_test_guard  # noqa: E402

cloud_test_guard.require_ready = lambda: None

from cloud_test.runtime import assert_cloud_test_runtime  # noqa: E402

identity = assert_cloud_test_runtime()
print("Cloud Test Runtime smoke: OK")
for key, value in identity.to_dict().items():
    print(f"  {key}: {value}")
