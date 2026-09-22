# -*- coding: utf-8 -*-
"""Compatibility tests: Local vs Synthetic shape parity (plan §54/§55/§44).

Only schema/type/state/error semantics are compared — never data values
(Local course names differ from synthetic ones by design).
"""
import dataclasses

import pytest

from cloud_test.adapters import (
    AuthError,
    Course,
    ExecutionResult,
    LocalBackendUnavailable,
    OrderRecord,
    QrSession,
    SessionUser,
    ValidationError,
    build_local_adapters,
    build_synthetic_adapters,
)
from cloud_test.adapters.local.backend import resolve_backend
from tests.cloud_test.adapter_cases import (
    SIDES,
    advance_qr,
    issue_session,
    make_sides,
    observe,
)


@pytest.fixture
def sides(tmp_path):
    bundle_map, fake = make_sides(tmp_path)
    return bundle_map, fake


class TestSchemaParity:
    def test_course_shape_identical(self, sides):
        bundles, _fake = sides
        shapes = {}
        for side in SIDES:
            courses = bundles[side].course.get_courses("zhs", account="SYN-USER-OK",
                                                       secret_ref="plain:pw")
            # Structural shape per course (counts may differ: 2 fake vs 3 synthetic).
            shapes[side] = {(type(c).__name__,
                             tuple(f.name for f in dataclasses.fields(c)),
                             tuple(type(getattr(c, f.name)).__name__
                                   for f in dataclasses.fields(c)))
                            for c in courses}
        assert shapes["local"] == shapes["synthetic"]
        assert len(shapes["local"]) == 1  # exactly one shape on each side
        # values may differ, types must not
        local_ids = {c.id for c in bundles["local"].course.get_courses("zhs", account="SYN-USER-OK",
                                                                      secret_ref="plain:pw")}
        syn_ids = {c.id for c in bundles["synthetic"].course.get_courses(
            "zhs", account="SYN-USER-OK", secret_ref="plain:pw")}
        assert local_ids != syn_ids  # by design (§55)

    def test_user_shape_identical(self, sides):
        bundles, _fake = sides
        users = {}
        for side in SIDES:
            auth = bundles[side].auth
            name = f"parity-{side}"
            user = auth.register(auth.reg_code, name, "pw-123456")
            users[side] = (type(user).__name__, [f.name for f in dataclasses.fields(user)],
                           isinstance(user.id, int), isinstance(user.is_admin, bool))
        assert users["local"] == users["synthetic"]
        assert isinstance(users["local"][0], str)

    def test_qr_session_shape_identical(self, sides):
        bundles, fake = sides
        out = {}
        for side in SIDES:
            oid = f"SYN-QR-PARITY-{side}"
            sess = bundles[side].qr.create_session(oid)
            out[side] = (type(sess).__name__, [f.name for f in dataclasses.fields(sess)],
                         sess.state, isinstance(sess.oid, str))
        assert out["local"] == out["synthetic"]

    def test_execution_result_shape_identical(self, sides):
        bundles, _fake = sides

        def run(side):
            b = bundles[side]
            order = OrderRecord(id=f"SYN-PARITY-{side}", user_id=1, platform="zhs",
                                account="SYN-RC-OK", courses="SYN-COURSE-001")
            return b.execution.execute(b.execution.prepare(order))

        local, synthetic = run("local"), run("synthetic")
        assert type(local) is type(synthetic) is ExecutionResult
        assert [f.name for f in dataclasses.fields(local)] == \
               [f.name for f in dataclasses.fields(synthetic)]
        assert isinstance(local.exit_code, int) and isinstance(synthetic.exit_code, int)
        assert local.log_ref and synthetic.log_ref

    def test_qr_state_vocabulary_matches(self, sides):
        bundles, _fake = sides
        for side in SIDES:
            oid = f"SYN-QR-VOCAB-{side}"
            b = bundles[side]
            b.qr.create_session(oid)
            states = [b.qr.get_status(oid)] + [advance_qr(b, oid) for _ in range(3)]
            assert set(states) <= {"waiting", "scanned", "confirmed", "expired",
                                   "canceled", "error", "unknown"}


class TestOrderRecordSafety:
    """plan §44 — no password/secret/cookie/token fields on the DTO."""

    def test_forbidden_fields_absent(self):
        names = {f.name for f in dataclasses.fields(OrderRecord)}
        for forbidden in ("password", "secret", "cookie", "cookies", "token", "credential"):
            assert forbidden not in names
        assert "secret_ref" not in names

    def test_execution_result_has_no_secret_fields(self):
        names = {f.name for f in dataclasses.fields(ExecutionResult)}
        assert names == {"exit_code", "status", "risk_flags", "log_ref"}


class TestDocumentedDifferences:
    """Differences that are intentional and must stay visible in the docs."""

    def test_settings_whitelist_differs_by_design(self, sides):
        bundles, _fake = sides
        # Local keeps the real knobs...
        bundles["local"].storage.set_setting("proxy_pool", "127.0.0.1:8080")
        assert bundles["local"].storage.get_setting("proxy_pool") == "127.0.0.1:8080"
        # ...cloud must refuse them (F19: shown as Not available).
        with pytest.raises(ValidationError):
            bundles["synthetic"].storage.set_setting("proxy_pool", "x")
        with pytest.raises(ValidationError):
            bundles["synthetic"].storage.set_setting("spoof", "1")
        with pytest.raises(ValidationError):
            bundles["synthetic"].storage.set_setting("jitter", "1")

    def test_synthetic_settings_allow_whitelisted_keys(self, sides):
        bundles, _fake = sides
        bundles["synthetic"].storage.set_setting("concurrency", "4")
        assert bundles["synthetic"].storage.get_setting("concurrency") == "4"

    def test_is_synthetic_flags_are_opposite(self, sides):
        bundles, _fake = sides
        for side, expected in (("local", False), ("synthetic", True)):
            for adapter in bundles[side].all():
                if adapter is not None:
                    assert adapter.is_synthetic is expected, adapter.name

    def test_identity_mechanisms_are_separate(self, sides):
        """Password hashing differs; sessions are minted by each side's own scheme."""
        bundles, _fake = sides
        local_pw = bundles["local"].auth.hash_password("same-password")
        syn_pw = bundles["synthetic"].auth.hash_password("same-password")
        assert local_pw != syn_pw
        for side in SIDES:
            auth = bundles[side].auth
            user = auth.register(auth.reg_code, f"session-{side}", "pw-abcdef")
            token = issue_session(auth, user.id)
            assert isinstance(token, str) and token
            assert auth.session_user(token).username == f"session-{side}"

    def test_logout_semantics_differ_by_design(self, sides):
        """Synthetic invalidates the token; local logout is stateless cookie deletion.

        order_platform.py:1792 only deletes the cookie, so the local adapter cannot
        invalidate a token server-side. The cloud side stores sessions, so it can.
        """
        bundles, _fake = sides
        syn = bundles["synthetic"].auth
        user = syn.register(syn.reg_code, "logout-syn", "pw-abcdef")
        token = syn.create_session(user.id)
        assert syn.session_user(token) is not None
        syn.logout(token)
        assert syn.session_user(token) is None

        local = bundles["local"].auth
        luser = local.register(local.reg_code, "logout-local", "pw-abcdef")
        ltoken = local.session_token(luser.id)
        assert local.session_user(ltoken) is not None
        local.logout(ltoken)
        assert local.session_user(ltoken) is not None  # stateless by design (documented)

    def test_local_qr_creation_requires_wiring(self):
        """Documented gap: local QR creation is still embedded in the Flask route."""
        bundle = build_local_adapters()
        with pytest.raises(Exception) as exc:
            bundle.qr.create_session("SYN-QR-UNWIRED")
        assert "api_qr_start" in str(exc.value)

    def test_local_backend_refused_under_cloud_test_mode(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TEST_MODE", "1")
        with pytest.raises(LocalBackendUnavailable):
            resolve_backend()

    def test_synthetic_bundle_never_requires_cloud_env(self, monkeypatch):
        monkeypatch.delenv("CLOUD_TEST_MODE", raising=False)
        bundle = build_synthetic_adapters()
        assert bundle.auth is not None and bundle.storage is not None
