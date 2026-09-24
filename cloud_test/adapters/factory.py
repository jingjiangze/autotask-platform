# -*- coding: utf-8 -*-
"""Adapter factories.

Two facts are kept structurally separate — never a mode flag inside one object:

    build_local_adapters()      -> adapters wrapping the real platform
    build_synthetic_adapters()  -> adapters backed by synthetic data

The synthetic factory verifies every adapter with ``assert_synthetic`` and
raises ``AdapterContractError`` otherwise (fail closed, plan §35/§68).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

from cloud_test.adapters.base import AdapterContractError, assert_synthetic
from cloud_test.adapters.local import (
    LocalAuthAdapter,
    LocalCourseAdapter,
    LocalExecutionAdapter,
    LocalLogSink,
    LocalQrAdapter,
    LocalStorageAdapter,
)
from cloud_test.adapters.synthetic import (
    SyntheticAuthAdapter,
    SyntheticCourseAdapter,
    SyntheticExecutionAdapter,
    SyntheticLogSink,
    SyntheticQrAdapter,
    SyntheticStorageAdapter,
)

__all__ = [
    "AdapterBundle",
    "assert_synthetic_bundle",
    "build_local_adapters",
    "build_synthetic_adapters",
]


@dataclass(frozen=True)
class AdapterBundle:
    auth: object
    course: object
    qr: object
    execution: object
    storage: object
    log: Optional[object] = None

    def all(self) -> Tuple[object, ...]:
        return (self.auth, self.course, self.qr, self.execution, self.storage, self.log)

    def by_name(self) -> dict:
        return {getattr(a, "name", type(a).__name__): a for a in self.all() if a is not None}


def assert_synthetic_bundle(bundle: AdapterBundle) -> None:
    """Fail closed unless every adapter in the bundle is synthetic."""
    for adapter in bundle.all():
        if adapter is None:
            continue
        assert_synthetic(adapter)


def build_synthetic_adapters(
    course_scenario: str = "success",
    qr_scenario: str = "confirm",
    execution_scenario: str = "SUCCESS",
    execution_duration_ms: int = 0,
    executive_steps: int = 3,
    execution_exit_code_map=None,
    clock=time.monotonic,
    sleep=time.sleep,
) -> AdapterBundle:
    """Cloud Test adapters: independent in-memory instances, verified synthetic."""
    bundle = AdapterBundle(
        auth=SyntheticAuthAdapter(),
        course=SyntheticCourseAdapter(scenario=course_scenario),
        qr=SyntheticQrAdapter(scenario=qr_scenario, clock=clock, sleep=sleep),
        execution=SyntheticExecutionAdapter(
            scenario=execution_scenario,
            duration_ms=execution_duration_ms,
            steps=executive_steps,
            exit_code_map=execution_exit_code_map,
            clock=clock,
            sleep=sleep,
        ),
        storage=SyntheticStorageAdapter(),
        log=SyntheticLogSink(),
    )
    assert_synthetic_bundle(bundle)
    return bundle


def build_local_adapters(
    backend=None,
    qr_create_hook=None,
    prepare_hook=None,
) -> AdapterBundle:
    """Local Production adapters wrapping the real platform.

    ``backend`` may be injected (tests use a fake). When omitted it is resolved
    lazily on first use, which is refused under CLOUD_TEST_MODE.
    """
    bundle = AdapterBundle(
        auth=LocalAuthAdapter(backend=backend),
        course=LocalCourseAdapter(backend=backend),
        qr=LocalQrAdapter(backend=backend, create_hook=qr_create_hook),
        execution=LocalExecutionAdapter(backend=backend, prepare_hook=prepare_hook),
        storage=LocalStorageAdapter(backend=backend),
        log=LocalLogSink(backend=backend),
    )
    for adapter in bundle.all():
        if adapter is not None and getattr(adapter, "is_synthetic", True):
            raise AdapterContractError(
                f"local factory produced a synthetic adapter: {adapter!r}"
            )
    return bundle
