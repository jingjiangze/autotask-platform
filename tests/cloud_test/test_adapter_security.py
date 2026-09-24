# -*- coding: utf-8 -*-
"""Security tests for the adapter layer (plan §45-§47, §64-§69).

Proves that synthetic adapters never reach the network, never touch real files
(orders/, cookies.json, cf/, secrets), never import order_platform, and that
the factory refuses non-synthetic adapters.
"""
import pathlib
import re
import socket
import subprocess
import sys
import urllib.request

import pytest

from cloud_test.adapters import (
    AdapterContractError,
    AdapterBundle,
    assert_synthetic,
    assert_synthetic_bundle,
    build_local_adapters,
    build_synthetic_adapters,
)
from cloud_test.adapters.base import AdapterContractError as _ContractError
from tests.cloud_test.adapter_cases import CASES, make_sides, observe

ADAPTERS_DIR = pathlib.Path(__file__).resolve().parents[2] / "cloud_test" / "adapters"


@pytest.fixture
def synthetic_bundle():
    return build_synthetic_adapters()


def _run_all_cases(bundle) -> None:
    for case in CASES:
        observe(lambda: case.run(bundle))


class TestNoNetwork:
    """plan §45 — synthetic adapters must never use network primitives."""

    @pytest.fixture
    def no_network(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("synthetic adapter attempted network access")

        monkeypatch.setattr(socket, "create_connection", boom)
        monkeypatch.setattr(socket.socket, "connect", boom)
        monkeypatch.setattr(socket.socket, "connect_ex", boom)
        monkeypatch.setattr(socket, "getaddrinfo", boom)
        monkeypatch.setattr(urllib.request, "urlopen", boom)
        monkeypatch.setattr(subprocess, "Popen", boom)
        monkeypatch.setattr(subprocess, "run", boom)
        return boom

    def test_synthetic_cases_run_with_all_transports_disabled(self, no_network, synthetic_bundle):
        _run_all_cases(synthetic_bundle)

    def test_synthetic_course_and_qr_never_open_sockets(self, no_network, synthetic_bundle):
        synthetic_bundle.course.get_courses("zhs", account="SYN-USER-OK", secret_ref="plain:x")
        synthetic_bundle.qr.create_session("SYN-QR-NET")
        synthetic_bundle.qr.qr_png("SYN-QR-NET")

    def test_synthetic_sources_contain_no_network_imports(self):
        forbidden = re.compile(r"^\s*(import|from)\s+(requests|urllib|http|socket|subprocess|"
                               r"asyncio|aiohttp|ftplib|smtplib|telnetlib)\b")
        offenders = []
        for path in (ADAPTERS_DIR / "synthetic").glob("*.py"):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if forbidden.match(line):
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        assert offenders == []


class TestNoRealFileAccess:
    """plan §46 — no orders/platform.db, no cookies.json, no cf/, no secrets."""

    @pytest.fixture
    def guarded_open(self, monkeypatch):
        real_open = open
        touched = []

        def guarded(file, *args, **kwargs):
            path = str(file)
            low = path.replace("\\", "/").lower()
            touched.append(low)
            if any(bad in low for bad in ("orders/", "platform.db", "cookies.json",
                                          "/cf/", "secrets", "credential")):
                raise AssertionError(f"synthetic adapter touched real path: {path}")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr("builtins.open", guarded)
        return touched

    def test_synthetic_cases_do_not_touch_real_paths(self, guarded_open, synthetic_bundle):
        _run_all_cases(synthetic_bundle)

    def test_synthetic_cases_do_not_open_the_production_db(self, guarded_open, synthetic_bundle):
        _ = synthetic_bundle.storage.list_orders(user_id=1)
        assert not [p for p in guarded_open if "platform.db" in p]


class TestBackendIsolation:
    """plan §47/§53 — injected backends only; order_platform never imported."""

    def test_order_platform_is_not_imported_by_adapter_tests(self, tmp_path):
        bundles, _fake = make_sides(tmp_path)
        _run_all_cases(bundles["local"])
        assert "order_platform" not in sys.modules, (
            "Local adapters must not import the side-effectful order_platform module"
        )

    def test_local_adapters_without_backend_refuse_under_cloud_mode(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TEST_MODE", "1")
        bundle = build_local_adapters()
        with pytest.raises(_ContractError):
            bundle.course.get_courses("zhs", account="x", secret_ref="plain:y")
        assert "order_platform" not in sys.modules

    def test_synthetic_bundles_are_independent(self):
        a = build_synthetic_adapters()
        b = build_synthetic_adapters()
        a.storage.create_order(1, "p", "zhs", "SYN-USER-OK", "plain:pw")
        assert a.storage.order_count == 1
        assert b.storage.order_count == 0
        user = a.auth.register(a.auth.reg_code, "independence-check", "pw")
        assert b.auth.session_user(a.auth.create_session(user.id)) is None


class TestSyntheticGuard:
    """plan §67/§68 — provenance enforcement."""

    def test_all_synthetic_adapters_pass_assert_synthetic(self):
        bundle = build_synthetic_adapters()
        checked = 0
        for adapter in bundle.all():
            if adapter is None:
                continue
            assert_synthetic(adapter)
            assert adapter.is_synthetic is True
            checked += 1
        assert checked == 6

    def test_fake_real_adapter_is_rejected(self):
        class FakeRealAdapter:
            name = "fake-real"
            is_synthetic = False

        bundle = AdapterBundle(auth=FakeRealAdapter(), course=build_synthetic_adapters().course,
                               qr=build_synthetic_adapters().qr,
                               execution=build_synthetic_adapters().execution,
                               storage=build_synthetic_adapters().storage)
        with pytest.raises(AdapterContractError, match="not synthetic"):
            assert_synthetic_bundle(bundle)

    def test_factory_rejects_synthetic_adapter_in_local_bundle(self, monkeypatch):
        """A local bundle must never contain an adapter claiming to be synthetic."""
        from cloud_test.adapters import factory

        class SyntheticLooking(factory.LocalAuthAdapter):
            is_synthetic = True

        monkeypatch.setattr(factory, "LocalAuthAdapter", SyntheticLooking)
        with pytest.raises(AdapterContractError, match="local factory produced a synthetic"):
            factory.build_local_adapters(backend=object())


class TestNoProductionDomains:
    """plan §65 — the adapter package must not name real platforms."""

    FORBIDDEN = ("zhihuishu.com", "chaoxing.com", "api.openai.com", "passport.")

    def test_no_production_domains_in_adapter_sources(self):
        offenders = []
        for path in ADAPTERS_DIR.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in self.FORBIDDEN:
                if needle in text:
                    offenders.append(f"{path.relative_to(ADAPTERS_DIR)}:{needle}")
        assert offenders == []
