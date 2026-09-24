# -*- coding: utf-8 -*-
"""Fail-closed egress guard for CLOUD_TEST_MODE.

Two layers are intentional:
1) kernel nftables deny policy installed before the runner starts;
2) Python hostname/socket guard inherited by Python subprocesses.

require_ready() never degrades to warning mode. If the kernel deny policy
cannot be verified, the cloud-test process must not start.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess

BLOCKED_HOSTS = (
    "zhihuishu.com",
    "chaoxing.com",
    "api.openai.com",
)

NFT_TABLE = "autotask_cloud_test"
MARKER_PATH = os.environ.get(
    "CLOUD_TEST_EGRESS_MARKER",
    "/tmp/autotask-cloud-test-egress.enabled",
)

class CloudTestEgressBlocked(ConnectionRefusedError):
    """Expected refusal for a blocked cloud-test destination."""

_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_ORIGINAL_SOCKET_CONNECT = socket.socket.connect
_ORIGINAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_ORIGINAL_CREATE_CONNECTION = socket.create_connection

_BLOCKED_IPS = set()
_INSTALLED = False


def _norm_host(host):
    return str(host or "").strip().rstrip(".").lower()


def is_blocked_host(host):
    h = _norm_host(host)
    return any(h == root or h.endswith("." + root) for root in BLOCKED_HOSTS)


def _addr_host(address):
    if isinstance(address, tuple) and address:
        return str(address[0])
    return ""


def _is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_blocked_ip(value):
    try:
        return str(ipaddress.ip_address(value)) in _BLOCKED_IPS
    except ValueError:
        return False


def _resolve_blocked_ips():
    ips = set()
    for host in BLOCKED_HOSTS:
        try:
            infos = _ORIGINAL_GETADDRINFO(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            continue
        for info in infos:
            sockaddr = info[4]
            if sockaddr:
                try:
                    ips.add(str(ipaddress.ip_address(sockaddr[0])))
                except ValueError:
                    pass
    return ips


def install_process_guard():
    global _INSTALLED, _BLOCKED_IPS
    if _INSTALLED:
        return
    _BLOCKED_IPS = _resolve_blocked_ips()

    def guarded_getaddrinfo(host, *args, **kwargs):
        if is_blocked_host(host):
            raise CloudTestEgressBlocked(
                f"CLOUD_TEST_MODE blocked destination: {_norm_host(host)}"
            )
        return _ORIGINAL_GETADDRINFO(host, *args, **kwargs)

    def guarded_connect(sock, address):
        host = _addr_host(address)
        if is_blocked_host(host) or _is_blocked_ip(host):
            raise CloudTestEgressBlocked(
                f"CLOUD_TEST_MODE blocked outbound connection: {host}"
            )
        return _ORIGINAL_SOCKET_CONNECT(sock, address)

    def guarded_connect_ex(sock, address):
        host = _addr_host(address)
        if is_blocked_host(host) or _is_blocked_ip(host):
            raise CloudTestEgressBlocked(
                f"CLOUD_TEST_MODE blocked outbound connection: {host}"
            )
        return _ORIGINAL_SOCKET_CONNECT_EX(sock, address)

    def guarded_create_connection(address, *args, **kwargs):
        host = _addr_host(address)
        if is_blocked_host(host) or _is_blocked_ip(host):
            raise CloudTestEgressBlocked(
                f"CLOUD_TEST_MODE blocked outbound connection: {host}"
            )
        return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)

    socket.getaddrinfo = guarded_getaddrinfo
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.create_connection = guarded_create_connection
    _INSTALLED = True


def process_guard_active():
    return _INSTALLED


def kernel_guard_active():
    if not os.path.exists(MARKER_PATH):
        return False
    try:
        p = subprocess.run(
            ["nft", "-j", "list", "table", "inet", NFT_TABLE],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False
    if p.returncode != 0:
        return False
    try:
        data = json.loads(p.stdout)
    except (TypeError, ValueError):
        return False

    sets = {}
    rules = []
    for obj in data.get("nftables", []):
        if "set" in obj:
            s = obj["set"]
            sets[s.get("name")] = s
        if "rule" in obj:
            rules.append(obj["rule"])

    v4 = sets.get("blocked_ipv4", {})
    v6 = sets.get("blocked_ipv6", {})
    elems = (v4.get("elem") or []) + (v6.get("elem") or [])
    has_reject = any(
        "reject" in json.dumps(rule, ensure_ascii=False).lower()
        for rule in rules
    )
    return bool(elems) and has_reject


def require_ready():
    """Hard gate for CLOUD_TEST_MODE."""
    if os.environ.get("CLOUD_TEST_MODE", "0") != "1":
        return

    install_process_guard()

    if os.environ.get("CLOUD_TEST_EGRESS_REQUIRED", "1") != "1":
        raise RuntimeError(
            "CLOUD_TEST_MODE requires CLOUD_TEST_EGRESS_REQUIRED=1"
        )

    if not kernel_guard_active():
        raise RuntimeError(
            "CLOUD_TEST_MODE egress guard is not ready: "
            "kernel nftables deny policy is missing or unverifiable"
        )


def assert_blocked(host, port=443):
    """Probe a blocked hostname. Connection must be refused."""
    install_process_guard()
    try:
        socket.create_connection((host, port), timeout=2)
    except CloudTestEgressBlocked:
        return
    raise AssertionError(
        f"blocked destination unexpectedly connected: {host}:{port}"
    )


def blocked_domains():
    return BLOCKED_HOSTS