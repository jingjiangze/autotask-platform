# -*- coding: utf-8 -*-
"""cloud_test.identity: immutable, environment-pinned runtime identity."""
import dataclasses
import re

import pytest

from cloud_test.identity import ENVIRONMENT, RuntimeIdentity, build_identity

UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
PY_VER = re.compile(r"^\d+\.\d+\.\d+$")


def make_identity(**kw):
    params = dict(runner_id="runner-01", runtime_id="11111111-2222-3333-4444-555555555555",
                  git_sha="a" * 40)
    params.update(kw)
    return build_identity(**params)


class TestEnvironmentPinned:
    def test_environment_is_cloud_test(self):
        assert make_identity().environment == "cloud-test"

    def test_environment_constant(self):
        assert ENVIRONMENT == "cloud-test"

    def test_cannot_construct_other_environment(self):
        with pytest.raises(ValueError):
            RuntimeIdentity(
                environment="production", runner_id="", runtime_id="x",
                git_sha="", started_at="", python_version="", platform="",
            )


class TestFields:
    def test_runner_id_correct(self):
        assert make_identity().runner_id == "runner-01"

    def test_runtime_id_non_empty(self):
        assert make_identity().runtime_id

    def test_runtime_id_unique_per_build(self):
        a = build_identity(runtime_id="", git_sha="")
        b = build_identity(runtime_id="", git_sha="")
        assert a.runtime_id and b.runtime_id and a.runtime_id != b.runtime_id

    def test_git_sha_injected(self):
        assert make_identity().git_sha == "a" * 40

    def test_started_at_utc_iso8601(self):
        assert UTC_ISO.match(make_identity().started_at)

    def test_python_version_format(self):
        assert PY_VER.match(make_identity().python_version)

    def test_platform_non_empty(self):
        assert make_identity().platform

    def test_to_dict_keys_exact(self):
        d = make_identity().to_dict()
        assert set(d) == {"environment", "runner_id", "runtime_id", "git_sha",
                          "started_at", "python_version", "platform"}
        # No secrets / env dumps / process info in metadata.
        blob = str(d)
        for forbidden in ("PATH=", "HTTP_PROXY", "token", "cookie", "password",
                          "Authorization", "Bearer"):
            assert forbidden not in blob, forbidden


class TestImmutability:
    def test_frozen_dataclass_rejects_mutation(self):
        ident = make_identity()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ident.environment = "production"

    def test_frozen_dataclass_rejects_runtime_id_mutation(self):
        ident = make_identity()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ident.runtime_id = "changed"
