import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import cloud_test_guard as guard


def test_blocked_hostname_detection():
    assert guard.is_blocked_host("zhihuishu.com")
    assert guard.is_blocked_host("hike.zhihuishu.com")
    assert guard.is_blocked_host("passport.chaoxing.com")
    assert guard.is_blocked_host("api.openai.com")
    assert not guard.is_blocked_host("example.com")


def test_process_guard_blocks_without_network_io():
    guard.install_process_guard()
    with pytest.raises(guard.CloudTestEgressBlocked):
        socket.create_connection(("hike.zhihuishu.com", 443), timeout=1)


def test_process_guard_allows_loopback():
    guard.install_process_guard()

    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    ready = threading.Event()

    def serve():
        ready.set()
        server.handle_request()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    ready.wait(timeout=1)

    with socket.create_connection(server.server_address, timeout=1) as s:
        s.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        assert s.recv(16).startswith(b"HTTP/")

    server.server_close()
    t.join(timeout=1)


def test_require_ready_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUD_TEST_MODE", "1")
    monkeypatch.setenv("CLOUD_TEST_EGRESS_REQUIRED", "1")
    monkeypatch.setenv("CLOUD_TEST_EGRESS_MARKER", str(tmp_path / "missing.marker"))
    monkeypatch.setattr(guard, "NFT_TABLE", "missing_cloud_test_table")
    guard.require_ready.__module__
    with pytest.raises(RuntimeError, match="kernel nftables deny policy"):
        guard.require_ready()


@pytest.mark.skipif(
    os.environ.get("CLOUD_TEST_EGRESS_KERNEL_TEST") != "1",
    reason="requires privileged Linux network namespace with nftables",
)
def test_kernel_guard_rejects_zhihuishu():
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy")}
    p = subprocess.run(
        ["curl", "--noproxy", "*", "--connect-timeout", "3",
         "https://hike.zhihuishu.com/"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        env=env,
    )
    assert p.returncode != 0, p.stdout + p.stderr


@pytest.mark.skipif(
    os.environ.get("CLOUD_TEST_EGRESS_KERNEL_TEST") != "1",
    reason="requires privileged Linux network namespace with nftables",
)
def test_kernel_guard_rejects_openai():
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy")}
    p = subprocess.run(
        ["curl", "--noproxy", "*", "--connect-timeout", "3",
         "https://api.openai.com/"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        env=env,
    )
    assert p.returncode != 0, p.stdout + p.stderr
