"""Hard-stop Python subprocesses when CLOUD_TEST_MODE is enabled.

The parent runner adds the repository root to PYTHONPATH. Python imports this
module during interpreter startup, so child Python processes inherit the same
fail-closed guard before the engine entry point runs.
"""
import os
import sys

if os.environ.get("CLOUD_TEST_MODE", "0") == "1":
    try:
        from cloud_test_guard import require_ready
        require_ready()
    except BaseException as exc:
        try:
            sys.stderr.write(
                "[CLOUD_TEST_MODE] egress guard failed closed: "
                f"{type(exc).__name__}: {exc}\n"
            )
            sys.stderr.flush()
        finally:
            os._exit(78)
