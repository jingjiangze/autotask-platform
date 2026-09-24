# -*- coding: utf-8 -*-
"""Adapter contract surface tests (Commit 06).

These lock the DECLARED contract (docs/ADAPTER_CONTRACT.md): method names, DTO
fields, error retryability and status vocabularies. Commit 07 will add the
behavioural contract suite that runs against Local and Cloud adapters alike
(tests/cloud_test/test_adapter_contract.py).
"""
import dataclasses
import pathlib
import re

import pytest

from cloud_test import adapters
from cloud_test.adapters import base, storage
from cloud_test.adapters.base import (
    AdapterContractError,
    AdapterError,
    AuthError,
    Course,
    ExecutionResult,
    NotFoundError,
    OrderRecord,
    PermanentError,
    Product,
    QrSession,
    QrState,
    RateLimitedError,
    SessionUser,
    TaskLogEntry,
    TaskStatus,
    TransientError,
    ValidationError,
    assert_synthetic,
)

ADAPTERS_DIR = pathlib.Path(adapters.__file__).parent


def methods_of(proto):
    """Declared surface = annotated attributes (across the MRO) + methods.

    ``dir()`` alone misses Protocol-declared annotations such as ``name`` /
    ``is_synthetic`` / ``order_id``, so annotations are merged in explicitly.
    """
    names = set()
    for klass in reversed(proto.__mro__):
        names |= set(klass.__dict__.get("__annotations__", {}))
    names |= {n for n in dir(proto) if not n.startswith("_")}
    return names


class TestAdapterSurfaces:
    def test_auth_adapter_exact_methods(self):
        assert methods_of(adapters.AuthAdapter) == {
            "register", "login", "session_user", "logout", "hash_password",
            "name", "is_synthetic",
        }

    def test_course_adapter_exact_methods(self):
        assert methods_of(adapters.CourseAdapter) == {"get_courses", "name", "is_synthetic"}

    def test_qr_adapter_exact_methods(self):
        assert methods_of(adapters.QrAdapter) == {
            "create_session", "qr_png", "get_status", "wait_confirmed", "cleanup",
            "name", "is_synthetic",
        }

    def test_execution_adapter_exact_methods(self):
        assert methods_of(adapters.ExecutionAdapter) == {
            "prepare", "execute", "cancel", "scan_risk", "last_log_ref",
            "name", "is_synthetic",
        }

    def test_storage_adapter_exact_methods(self):
        assert methods_of(adapters.StorageAdapter) == {
            "get_products", "create_order", "get_order", "list_orders",
            "find_order_by_prefix", "update_order", "submit_order",
            "get_setting", "set_setting", "name", "is_synthetic",
        }

    def test_log_sink_exact_methods(self):
        assert methods_of(adapters.LogSink) == {"append", "read_tail", "list_entries",
                                                "name", "is_synthetic"}

    def test_execution_context_exact_methods(self):
        assert methods_of(adapters.ExecutionContext) == {
            "heartbeat", "log", "progress", "order_id", "attempt",
        }

    def test_course_platform_vocabulary_frozen(self):
        assert adapters.course.PLATFORMS == ("chaoxing", "zhs", "zhs_cookie")

    def test_risk_categories_frozen(self):
        assert adapters.execution.RISK_CATEGORIES == (
            "captcha", "forbidden", "risk_ctrl", "login_fail", "network")


class TestErrorTaxonomy:
    @pytest.mark.parametrize("exc,retryable", [
        (ValidationError, False),
        (AuthError, False),
        (NotFoundError, False),
        (RateLimitedError, True),
        (TransientError, True),
        (PermanentError, False),
        (AdapterContractError, False),
    ])
    def test_retryable_flags(self, exc, retryable):
        assert exc.retryable is retryable

    def test_all_errors_subclass_adapter_error(self):
        for exc in (ValidationError, AuthError, NotFoundError, RateLimitedError,
                    TransientError, PermanentError, AdapterContractError):
            assert issubclass(exc, AdapterError)

    def test_only_transient_and_rate_limited_are_retryable(self):
        retryable = {c.__name__ for c in
                     (ValidationError, AuthError, NotFoundError, RateLimitedError,
                      TransientError, PermanentError, AdapterContractError)
                     if c.retryable}
        assert retryable == {"RateLimitedError", "TransientError"}


class TestStatusVocabularies:
    def test_task_status_values(self):
        assert TaskStatus.ALL == ("PENDING", "CLAIMED", "RUNNING", "DONE",
                                  "FAILED", "RETRY_WAIT", "CANCELED")

    def test_legacy_status_map_covers_local_states(self):
        # exactly the six states used by orders.status today (F-table §2)
        assert set(TaskStatus.LEGACY_MAP) == {
            "pending", "waiting_qr", "running", "done", "failed", "canceled"}
        assert TaskStatus.LEGACY_MAP["waiting_qr"] == TaskStatus.PENDING

    def test_qr_states_match_local_qr_state_semantics(self):
        assert QrState.ALL == ("waiting", "scanned", "confirmed", "expired",
                               "canceled", "error", "unknown")
        assert set(QrState.TERMINAL) == {"confirmed", "expired", "canceled", "error"}


class TestDtos:
    def test_dtos_are_frozen(self):
        for dto in (Product, Course, SessionUser, QrSession, OrderRecord,
                    ExecutionResult, TaskLogEntry, adapters.HealthStatus):
            assert dataclasses.is_dataclass(dto), dto
            assert dto.__dataclass_params__.frozen, dto

    def test_order_record_has_no_password_field(self):
        names = {f.name for f in dataclasses.fields(OrderRecord)}
        assert "password" not in names
        assert "secret_ref" not in names  # never persisted in the DTO
        assert {"id", "user_id", "status", "attempt", "runner_id", "lease_id"} <= names

    def test_course_requires_id_and_name(self):
        with pytest.raises(ValidationError):
            Course(id="", name="x")
        with pytest.raises(ValidationError):
            Course(id="SYN-COURSE-001", name="")

    def test_qr_session_rejects_unknown_state(self):
        with pytest.raises(ValidationError):
            QrSession(oid="abc", state="bogus")
        assert QrSession(oid="abc").state == QrState.WAITING


class TestExecutionResultSemantics:
    @pytest.mark.parametrize("rc,retryable", [(0, False), (-9, True), (-1, True), (1, False), (2, False)])
    def test_exit_code_classification(self, rc, retryable):
        assert ExecutionResult(exit_code=rc).retryable is retryable

    def test_positive_rc_maps_to_permanent_error(self):
        assert isinstance(ExecutionResult(exit_code=3).to_error(), PermanentError)

    def test_timeout_maps_to_transient_error(self):
        err = ExecutionResult(exit_code=-9).to_error()
        assert isinstance(err, TransientError) and err.retryable

    def test_success_has_no_error(self):
        with pytest.raises(ValueError):
            ExecutionResult(exit_code=0).to_error()


class TestSyntheticGuard:
    def test_assert_synthetic_accepts_synthetic(self):
        class A:
            name = "synthetic-x"
            is_synthetic = True
        assert_synthetic(A())

    def test_assert_synthetic_rejects_real_adapter(self):
        class A:
            name = "local-real"
            is_synthetic = False
        with pytest.raises(AdapterContractError, match="not synthetic"):
            assert_synthetic(A())

    def test_assert_synthetic_rejects_missing_flag(self):
        class A:
            name = "unknown"
        with pytest.raises(AdapterContractError):
            assert_synthetic(A())


class TestSettingsWhitelists:
    def test_local_only_and_cloud_settings_disjoint(self):
        assert not set(storage.LOCAL_ONLY_SETTINGS) & set(storage.CLOUD_SETTINGS)

    def test_local_only_covers_v3_section38_items(self):
        assert set(storage.LOCAL_ONLY_SETTINGS) == {"proxy_pool", "spoof", "jitter"}


class TestNoProductionDomainsInContracts:
    FORBIDDEN = ("zhihuishu.com", "chaoxing.com", "api.openai.com", "passport.")

    def test_no_production_urls_in_adapter_contracts(self):
        offenders = []
        for path in ADAPTERS_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in self.FORBIDDEN:
                if needle in text:
                    offenders.append(f"{path.name}:{needle}")
        assert offenders == []

    def test_contract_modules_are_declarative_only(self):
        """The six contract modules stay IO-free.

        Commit 07 added implementations under ``local/`` and ``synthetic/`` plus a
        ``factory.py``; the *contract* modules must still declare only
        types/protocols (no network, no subprocess, no filesystem, no DB).
        """
        contract_modules = ("base.py", "auth.py", "course.py", "qr.py",
                            "execution.py", "storage.py")
        forbidden = re.compile(r"^\s*(import|from)\s+(os|socket|subprocess|sqlite3|urllib|"
                               r"requests|http|shutil|pathlib|tempfile|json|time)\b")
        for name in contract_modules:
            text = (ADAPTERS_DIR / name).read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                assert not forbidden.match(line), f"{name}:{lineno}: {line.strip()}"
                assert "open(" not in line, f"{name}:{lineno}: {line.strip()}"
