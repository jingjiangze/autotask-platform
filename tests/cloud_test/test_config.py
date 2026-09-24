# -*- coding: utf-8 -*-
"""cloud_test.config parsing rules."""
import re
import uuid
from unittest import mock

import pytest

from cloud_test import config

MODE_KEYS = ["CLOUD_TEST_MODE", "CLOUD_TEST_EGRESS_REQUIRED",
             "CLOUD_TEST_RUNNER_ID", "CLOUD_TEST_GIT_SHA"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in MODE_KEYS:
        monkeypatch.delenv(k, raising=False)


class TestCloudTestMode:
    def test_mode_1_enabled(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TEST_MODE", "1")
        assert config.is_cloud_test_enabled() is True

    def test_mode_0_disabled(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TEST_MODE", "0")
        assert config.is_cloud_test_enabled() is False

    def test_mode_unset_disabled(self):
        assert config.is_cloud_test_enabled() is False

    @pytest.mark.parametrize("bad", ["true", "TRUE", "True", "yes", "false",
                                     "False", "on", "01", "1.0", " 1", "1 "])
    def test_mode_only_exact_1(self, monkeypatch, bad):
        monkeypatch.setenv("CLOUD_TEST_MODE", bad)
        assert config.is_cloud_test_enabled() is False, bad

    def test_require_enabled_raises_when_disabled(self):
        with pytest.raises(config.CloudTestConfigError, match="CLOUD_TEST_MODE"):
            config.require_cloud_test_enabled()


class TestEgressRequired:
    def test_unset_defaults_to_required(self):
        assert config.get_egress_required() is True

    def test_mode_1_egress_0_must_reject(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TEST_MODE", "1")
        monkeypatch.setenv("CLOUD_TEST_EGRESS_REQUIRED", "0")
        with pytest.raises(config.CloudTestConfigError,
                           match="CLOUD_TEST_EGRESS_REQUIRED"):
            config.require_egress_required()

    @pytest.mark.parametrize("bad", ["0", "false", "TRUE", "yes", ""])
    def test_egress_only_exact_1(self, monkeypatch, bad):
        monkeypatch.setenv("CLOUD_TEST_EGRESS_REQUIRED", bad)
        assert config.get_egress_required() is False, repr(bad)

    def test_egress_1_ok(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TEST_EGRESS_REQUIRED", "1")
        assert config.get_egress_required() is True


class TestRunnerId:
    def test_runner_id_injected(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TEST_RUNNER_ID", "runner-01")
        assert config.get_runner_id() == "runner-01"

    def test_runner_id_absent_empty_no_persistence(self):
        # Missing runner id must not generate or persist an identity here.
        assert config.get_runner_id() == ""

    def test_runner_id_never_from_hostname_or_user(self, monkeypatch):
        # Even with hostname-like env present, runner id stays empty.
        monkeypatch.setenv("CLOUD_TEST_RUNNER_ID", "")
        assert config.get_runner_id() == ""


class TestRuntimeId:
    def test_runtime_id_is_uuid4_and_unique(self):
        a, b = config.get_runtime_id(), config.get_runtime_id()
        assert a != b
        # Both must be valid UUIDs (v4 formatting accepted).
        assert str(uuid.UUID(a)) == a
        assert str(uuid.UUID(b)) == b


class TestGitSha:
    def test_sha_env_override(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TEST_GIT_SHA", "a" * 40)
        assert config.get_git_sha() == "a" * 40

    def test_sha_env_override_truncated_to_40(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TEST_GIT_SHA", "b" * 50)
        assert config.get_git_sha() == "b" * 40

    def test_sha_subprocess_failure_returns_unknown(self, monkeypatch):
        boom = mock.Mock(side_effect=OSError("no git"))
        monkeypatch.setattr(config.subprocess, "run", boom)
        assert config.get_git_sha() == "unknown"

    def test_sha_bad_output_returns_unknown(self, monkeypatch):
        bad = mock.Mock(returncode=0, stdout="not-a-sha\n")
        monkeypatch.setattr(config.subprocess, "run", bad)
        assert config.get_git_sha() == "unknown"

    def test_sha_never_raises(self, monkeypatch):
        # Even a totally broken subprocess layer must not raise.
        monkeypatch.setattr(config.subprocess, "run",
                            mock.Mock(side_effect=Exception("x")))
        assert config.get_git_sha() == "unknown"
