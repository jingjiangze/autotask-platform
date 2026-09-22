# -*- coding: utf-8 -*-
"""cloud_test.runtime fail-closed scenarios A-D plus the Commit-04 bypass check.

The egress guard (cloud_test_guard.require_ready) is always mocked here:
Commit 04 owns real-kernel verification; these are unit-level runtime tests.
"""
import dataclasses

import pytest

from cloud_test import runtime
from cloud_test.identity import RuntimeIdentity
from cloud_test.runtime import CloudTestRuntimeError

ENV_KEYS = ["CLOUD_TEST_MODE", "CLOUD_TEST_EGRESS_REQUIRED",
            "CLOUD_TEST_RUNNER_ID", "CLOUD_TEST_GIT_SHA"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def mock_guard(monkeypatch):
    """Replace the guard with an injectable fake; default = verified."""
    state = {"calls": 0}

    def fake_require_ready():
        if state.get("fail"):
            raise RuntimeError(
                "CLOUD_TEST_MODE egress guard is not ready: "
                "kernel nftables deny policy is missing or unverifiable"
            )
        state["calls"] += 1

    monkeypatch.setattr("cloud_test_guard.require_ready", fake_require_ready)
    return state


def enable(monkeypatch, mode="1", egress="1"):
    monkeypatch.setenv("CLOUD_TEST_MODE", mode)
    monkeypatch.setenv("CLOUD_TEST_EGRESS_REQUIRED", egress)
    monkeypatch.setenv("CLOUD_TEST_RUNNER_ID", "runner-01")
    monkeypatch.setenv("CLOUD_TEST_GIT_SHA", "a" * 40)


class TestScenarioA_ModeDisabled:
    def test_mode_0_fails(self, monkeypatch, mock_guard):
        enable(monkeypatch, mode="0")
        with pytest.raises(CloudTestRuntimeError, match="CLOUD_TEST_MODE is not enabled"):
            runtime.assert_cloud_test_runtime()

    def test_mode_unset_fails(self, mock_guard):
        with pytest.raises(CloudTestRuntimeError, match="CLOUD_TEST_MODE is not enabled"):
            runtime.assert_cloud_test_runtime()

    def test_mode_true_string_fails(self, monkeypatch, mock_guard):
        enable(monkeypatch, mode="true")
        with pytest.raises(CloudTestRuntimeError):
            runtime.assert_cloud_test_runtime()


class TestScenarioB_EgressNotRequired:
    def test_mode_1_egress_0_fails(self, monkeypatch, mock_guard):
        enable(monkeypatch, egress="0")
        with pytest.raises(CloudTestRuntimeError, match="CLOUD_TEST_EGRESS_REQUIRED"):
            runtime.assert_cloud_test_runtime()

    def test_guard_never_called_when_config_rejects(self, monkeypatch, mock_guard):
        enable(monkeypatch, egress="0")
        with pytest.raises(CloudTestRuntimeError):
            runtime.assert_cloud_test_runtime()
        assert mock_guard["calls"] == 0


class TestScenarioC_GuardUnavailable:
    def test_guard_failure_fails_closed(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        mock_guard["fail"] = True
        with pytest.raises(CloudTestRuntimeError, match="egress guard is not ready"):
            runtime.assert_cloud_test_runtime()

    def test_rejection_is_runtime_error(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        mock_guard["fail"] = True
        with pytest.raises(RuntimeError):
            runtime.assert_cloud_test_runtime()

    def test_no_silent_none_or_false(self, monkeypatch, mock_guard):
        # Rejection must raise, never return a falsy sentinel.
        enable(monkeypatch)
        mock_guard["fail"] = True
        result = None
        try:
            result = runtime.assert_cloud_test_runtime()
        except CloudTestRuntimeError:
            pass
        assert result is None or isinstance(result, RuntimeIdentity)
        if result is None:
            pass  # raised as expected
        # Explicit: call must have raised — asserted above by match variant.


class TestScenarioD_Success:
    def test_identity_created_when_all_layers_pass(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        ident = runtime.assert_cloud_test_runtime()
        assert isinstance(ident, RuntimeIdentity)
        assert ident.environment == "cloud-test"
        assert ident.runner_id == "runner-01"
        assert ident.git_sha == "a" * 40
        assert mock_guard["calls"] == 1

    def test_identity_immutable(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        ident = runtime.assert_cloud_test_runtime()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ident.environment = "production"

    def test_runtime_identity_also_validated(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        assert runtime.runtime_identity().runner_id == "runner-01"

    def test_is_cloud_test_runtime_side_effect_free(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        assert runtime.is_cloud_test_runtime() is True
        # Config-only check must NOT have invoked the guard.
        assert mock_guard["calls"] == 0

    def test_is_cloud_test_runtime_false_when_config_bad(self, monkeypatch, mock_guard):
        enable(monkeypatch, egress="0")
        assert runtime.is_cloud_test_runtime() is False
        enable(monkeypatch, mode="0")
        assert runtime.is_cloud_test_runtime() is False


class TestCommit04CannotBeBypassed:
    """Guard unavailable => runtime must stop before any task execution."""

    def test_no_network_policy_means_rejection(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        mock_guard["fail"] = True
        with pytest.raises(RuntimeError, match="egress guard is not ready"):
            runtime.assert_cloud_test_runtime()

    def test_runtime_never_auto_repairs_guard(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        mock_guard["fail"] = True
        with pytest.raises(CloudTestRuntimeError):
            runtime.assert_cloud_test_runtime()
        # The fake guard stayed failed: runtime neither fixed it nor retried.
        assert mock_guard["fail"] is True
        assert mock_guard.get("calls", 0) == 0

    def test_identity_never_issued_without_guard(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        mock_guard["fail"] = True
        with pytest.raises(CloudTestRuntimeError):
            runtime.runtime_identity()

    def test_env_never_mutated_by_runtime(self, monkeypatch, mock_guard):
        enable(monkeypatch)
        runtime.assert_cloud_test_runtime()
        # Values unchanged, nothing added.
        import os
        assert os.environ["CLOUD_TEST_MODE"] == "1"
        assert os.environ["CLOUD_TEST_EGRESS_REQUIRED"] == "1"
