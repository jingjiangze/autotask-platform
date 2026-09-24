# -*- coding: utf-8 -*-
"""Unified contract tests: the SAME cases run against Local and Synthetic.

Layer A (all environments): DTO/error/state/validation via the synthetic side.
Layer B (fixture mode): the local side runs against an in-memory fake backend —
never the real platform, never orders/platform.db (plan §37/§38/§52).
"""
import pytest

from cloud_test.adapters import AdapterError
from tests.cloud_test.adapter_cases import CASES, SIDES, make_sides, observe


@pytest.fixture
def sides(tmp_path):
    bundle_map, fake = make_sides(tmp_path)
    return bundle_map, fake


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
@pytest.mark.parametrize("side", SIDES)
def test_case_complies_with_contract(sides, side, case):
    bundles, _fake = sides
    kind, *rest = observe(lambda: case.run(bundles[side]))
    if case.expect == "ok":
        assert kind == "ok", f"{case.name}/{side} raised: {rest}"
        if case.check is not None:
            assert case.check(rest[0]), f"{case.name}/{side}: shape check failed: {rest[0]!r}"
    else:
        assert kind == "err", f"{case.name}/{side} should fail with {case.error}, got {kind} {rest}"
        exc_name, retryable, code = rest
        assert exc_name == case.error, f"{case.name}/{side}: {exc_name} != {case.error}"
        assert isinstance(retryable, bool)
        assert bool(code), f"{case.name}/{side}: empty error code"
        if case.retryable is not None:
            assert retryable is case.retryable


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_outcomes_identical_across_sides(sides, case):
    """The core promise: Local and Cloud fail the same way, succeed the same way."""
    bundles, _fake = sides
    local = observe(lambda: case.run(bundles["local"]))
    synthetic = observe(lambda: case.run(bundles["synthetic"]))
    assert local[0] == synthetic[0], f"{case.name}: {local} vs {synthetic}"
    if local[0] == "err":
        assert local[1] == synthetic[1], f"{case.name}: error type differs"
        assert local[2] is synthetic[2], f"{case.name}: retryability differs"
        assert local[3] == synthetic[3] or case.error == "ValidationError", \
            f"{case.name}: error code differs: {local[3]} vs {synthetic[3]}"


class TestErrorTaxonomyRules:
    """plan §41/§42 — the scheduler will depend on exactly these rules."""

    RETRYABLE = ("TransientError", "RateLimitedError")
    NOT_RETRYABLE = ("ValidationError", "AuthError", "NotFoundError", "PermanentError",
                     "AdapterContractError")

    @pytest.mark.parametrize("case", [c for c in CASES if c.expect == "err"],
                             ids=[c.name for c in CASES if c.expect == "err"])
    def test_error_contract_fields(self, sides, case):
        bundles, _fake = sides
        for side in SIDES:
            outcome = observe(lambda: case.run(bundles[side]))
            assert outcome[0] == "err"
            assert isinstance(outcome[1], str) and outcome[1].endswith("Error")
            assert isinstance(outcome[2], bool)
            assert isinstance(outcome[3], str) and outcome[3] == outcome[3].lower()

    def test_retryable_is_true_only_for_transient_and_rate_limited(self, sides):
        bundles, _fake = sides
        seen = {}
        for case in CASES:
            if case.expect != "err":
                continue
            outcome = observe(lambda: case.run(bundles["synthetic"]))
            seen[outcome[1]] = outcome[2]
        for name, retryable in seen.items():
            if name in self.RETRYABLE:
                assert retryable is True, name
            else:
                assert retryable is False, name


class TestExecutionExitCodeContract:
    """plan §43 — identical to Commit 06, verified on both sides."""

    EXPECT = {"execution_success": 0, "execution_crash": -1, "execution_timeout": -9,
              "execution_business_error": 1}

    @pytest.mark.parametrize("case_name,rc", sorted(EXPECT.items()))
    @pytest.mark.parametrize("side", SIDES)
    def test_exit_code_semantics(self, sides, side, case_name, rc):
        bundles, _fake = sides
        case = next(c for c in CASES if c.name == case_name)
        outcome = observe(lambda: case.run(bundles[side]))
        assert outcome[0] == "ok"
        result = outcome[1]
        assert result.exit_code == rc
        expected_retryable = rc == -9 or rc < 0
        assert result.retryable is expected_retryable
        if rc != 0:
            assert isinstance(result.to_error(), AdapterError)
            assert result.to_error().retryable is expected_retryable
