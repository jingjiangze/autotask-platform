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
    monkeypatch.setattr(
        guard, "MARKER_PATH", str(tmp_path / "missing.marker")
    )
    monkeypatch.setattr(guard, "NFT_TABLE", "missing_cloud_test_table")

    with pytest.raises(RuntimeError, match="kernel nftables deny policy"):
        guard.require_ready()


# ---------------------------------------------------------------------------
# 回归护栏：kernel_guard_active() 必须能真实解析 nft -j 输出，且任何解析
# 异常都不得逃逸（必须降级为 False，而不是让 require_ready 以 NameError 崩掉）。
# 背景：真机验收（Commit 04, run 35685636581）发现缺 import json —— Windows
# 本地测试全绿是因为 marker 不存在时提前 return False，从未走到 json.loads。
# ---------------------------------------------------------------------------
import json as _json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_FAKE_NFT_OK = {
    "nftables": [
        {"metainfo": {"version": "1.0.9"}},
        {"table": {"family": "inet", "name": "autotask_cloud_test"}},
        {"set": {"family": "inet", "name": "blocked_ipv4",
                 "table": "autotask_cloud_test", "flags": ["interval"],
                 "elem": ["8.139.74.193", "140.210.88.44"]}},
        {"set": {"family": "inet", "name": "blocked_ipv6",
                 "table": "autotask_cloud_test", "flags": ["interval"],
                 "elem": ["2606:4700:7::f3"]}},
        {"rule": {"family": "inet", "table": "autotask_cloud_test",
                  "chain": "output",
                  "expr": [{"reject": {"type": "icmp"}}]}},
    ]
}


def _fake_run(payload, returncode=0):
    def _run(*args, **kwargs):
        return SimpleNamespace(returncode=returncode,
                               stdout=_json.dumps(payload), stderr="")
    return _run


def test_kernel_guard_parses_real_nft_json(monkeypatch, tmp_path):
    marker = tmp_path / "marker"
    marker.write_text("enabled=test")
    monkeypatch.setattr(guard, "MARKER_PATH", str(marker))
    monkeypatch.setattr(guard, "NFT_TABLE", "autotask_cloud_test")
    monkeypatch.setattr(guard.subprocess, "run", _fake_run(_FAKE_NFT_OK))
    assert guard.kernel_guard_active() is True


def test_kernel_guard_false_when_sets_empty(monkeypatch, tmp_path):
    marker = tmp_path / "marker"
    marker.write_text("enabled=test")
    monkeypatch.setattr(guard, "MARKER_PATH", str(marker))
    monkeypatch.setattr(guard, "NFT_TABLE", "autotask_cloud_test")
    empty = {"nftables": [_FAKE_NFT_OK["nftables"][1]]}
    # 只留 table 对象：无 set、无 rule → elems 为空 → False
    monkeypatch.setattr(guard.subprocess, "run", _fake_run(empty))
    assert guard.kernel_guard_active() is False


def test_kernel_guard_false_when_nft_fails(monkeypatch, tmp_path):
    marker = tmp_path / "marker"
    marker.write_text("enabled=test")
    monkeypatch.setattr(guard, "MARKER_PATH", str(marker))
    monkeypatch.setattr(guard, "NFT_TABLE", "no_such_table")
    monkeypatch.setattr(guard.subprocess, "run",
                        _fake_run({"nftables": []}, returncode=1))
    assert guard.kernel_guard_active() is False


def test_kernel_guard_never_raises(monkeypatch, tmp_path):
    """nft 输出损坏 / 解析异常时必须降级为 False，而不是抛异常。"""
    marker = tmp_path / "marker"
    marker.write_text("enabled=test")
    monkeypatch.setattr(guard, "MARKER_PATH", str(marker))
    monkeypatch.setattr(guard, "NFT_TABLE", "autotask_cloud_test")
    monkeypatch.setattr(guard.subprocess, "run",
                        _fake_run({"nftables": [{"bogus": {}}]}))
    assert guard.kernel_guard_active() is False


@pytest.mark.skipif(
    os.environ.get("CLOUD_TEST_EGRESS_KERNEL_TEST") != "1",
    reason="requires privileged Linux network namespace with nftables",
)
def test_kernel_guard_rejects_zhihuishu():
    import subprocess

    env = {
        k: v for k, v in os.environ.items()
        if k.lower() not in ("http_proxy", "https_proxy")
    }
    p = subprocess.run(
        [
            "curl", "--noproxy", "*", "--connect-timeout", "3",
            "https://hike.zhihuishu.com/",
        ],
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

    env = {
        k: v for k, v in os.environ.items()
        if k.lower() not in ("http_proxy", "https_proxy")
    }
    p = subprocess.run(
        [
            "curl", "--noproxy", "*", "--connect-timeout", "3",
            "https://api.openai.com/",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        env=env,
    )
    assert p.returncode != 0, p.stdout + p.stderr
