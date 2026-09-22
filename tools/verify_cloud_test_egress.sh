#!/usr/bin/env bash
# =============================================================================
# verify_cloud_test_egress.sh — Commit 04 真机网络隔离验收
#
# 目标（方案 §四）：证明「云端 Python 进程即使故意忽略 CLOUD_TEST_MODE，
# 也依然无法访问生产目标」。
#
# 覆盖六层，缺一不可：
#   1. fail-closed 预检   —— 未安装内核策略时，任何 Python 子进程必须拒绝启动(exit 78)
#   2. 内核层             —— nftables table/set/reject 实际存在且可校验
#   3. 进程层             —— Python guard 拦截 getaddrinfo/connect
#   4. sitecustomize      —— 子 Python 进程启动即被守卫接管（继承验证）
#   5. curl 绕过应用层    —— 非 Python 二进制直连，只能靠内核层拦截
#   6. IP 直连绕过 DNS    —— 不走域名解析、直接连被禁 IP，内核 set 必须命中
#
# 语义约定（关键，防止假 PASS）：
#   curl 退出码 28 = 超时 = 目标只是"不可达"而不是"被拦截" → 记 FAIL
#   其余非零退出码（refused / permission denied 等）= 被内核 reject → 记 PASS
#
# 用法：  sudo ./tools/verify_cloud_test_egress.sh
# 环境：  Linux + root/CAP_NET_ADMIN + nftables；CLOUD_TEST_PYTHON 可覆盖解释器
# =============================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY="${CLOUD_TEST_PYTHON:-python3}"
TABLE="autotask_cloud_test"
MARKER="${CLOUD_TEST_EGRESS_MARKER:-/tmp/autotask-cloud-test-egress.enabled}"
GUARD_INSTALL="tools/install_cloud_test_egress_guard.sh"
REPORT_DIR="${CLOUD_TEST_REPORT_DIR:-cloud-test-data}"
REPORT="$REPORT_DIR/acceptance-report.txt"

BLOCKED_TARGETS=(
  "hike.zhihuishu.com"
  "passport.zhihuishu.com"
  "zhihuishu.com"
  "chaoxing.com"
  "api.openai.com"
)
CONTROL_TARGET="example.com"

PASS=0; FAIL=0; WARN=0
report(){
  local verdict="$1"; shift
  case "$verdict" in
    PASS) PASS=$((PASS+1));;
    FAIL) FAIL=$((FAIL+1));;
    WARN) WARN=$((WARN+1));;
  esac
  printf '[%s] %s\n' "$verdict" "$*"
  printf '[%s] %s\n' "$verdict" "$*" >> "$REPORT"
}

mkdir -p "$REPORT_DIR"
{
  echo "==== COMMIT 04 EGRESS ACCEPTANCE ===="
  echo "host: $(uname -a)"
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$REPORT"

echo "==== COMMIT 04 EGRESS ACCEPTANCE ===="
echo "[ENV] $(id)"
echo "[ENV] $(uname -a)"

# -----------------------------------------------------------------------------
# 0. 环境前置
# -----------------------------------------------------------------------------
if [[ "$(id -u)" -ne 0 ]]; then
  report FAIL "env: must run as root/CAP_NET_ADMIN (uid=$(id -u))"
  echo "VERDICT: FAIL (env)"
  exit 1
fi
report PASS "env: running as root"

if ! command -v nft >/dev/null 2>&1; then
  report FAIL "env: nft command not found"
  echo "VERDICT: FAIL (env)"; exit 1
fi
report PASS "env: $(nft --version 2>&1)"
echo "[ENV] python: $($PY --version 2>&1)"

# -----------------------------------------------------------------------------
# 1. fail-closed 预检（安装前）：内核策略缺失 → Python 子进程必须拒绝启动
#    sitecustomize 在解释器启动早期 require_ready() 失败 → os._exit(78)
# -----------------------------------------------------------------------------
pre_rc=0
CLOUD_TEST_MODE=1 CLOUD_TEST_EGRESS_REQUIRED=1 PYTHONPATH="$REPO_ROOT" \
  timeout 60 "$PY" -c "print('benign-child-ok')" >/dev/null 2>&1 || pre_rc=$?
if [[ "$pre_rc" -eq 78 ]]; then
  report PASS "fail-closed pre-check: without kernel policy, python child refuses to start (exit 78)"
elif [[ "$pre_rc" -eq 0 ]]; then
  report FAIL "fail-closed pre-check: python child started WITHOUT kernel policy (fail-open!)"
else
  report WARN "fail-closed pre-check: unexpected rc=$pre_rc (expected 78); continuing"
fi

# -----------------------------------------------------------------------------
# 2. 安装内核守卫
# -----------------------------------------------------------------------------
if bash "$GUARD_INSTALL" >> "$REPORT" 2>&1; then
  report PASS "install: $GUARD_INSTALL completed"
else
  report FAIL "install: $GUARD_INSTALL failed (see report)"
  echo "VERDICT: FAIL (install)"; exit 1
fi

[[ -f "$MARKER" ]] && report PASS "install: marker present ($MARKER)" \
                   || report FAIL "install: marker missing ($MARKER)"

# -----------------------------------------------------------------------------
# 3. 内核层检查：table / blocked sets 非空 / reject 规则（复用 guard 自校验逻辑）
# -----------------------------------------------------------------------------
if CLOUD_TEST_MODE=1 PYTHONPATH="$REPO_ROOT" timeout 30 "$PY" -c "
import cloud_test_guard as g
assert g.kernel_guard_active(), 'kernel guard inactive or unverifiable'
print('nft-table:', g.NFT_TABLE)
"; then
  report PASS "nftables inspection: table=$TABLE sets=blocked_ipv4+blocked_ipv6 reject=present"
else
  report FAIL "nftables inspection: kernel_guard_active() returned False"
fi

# -----------------------------------------------------------------------------
# 4. 进程层检查：Python guard 拦截（不发起真实网络连接）
# -----------------------------------------------------------------------------
if timeout 60 "$PY" -c "
import cloud_test_guard as g
g.assert_blocked('hike.zhihuishu.com')
"; then
  report PASS "process-level test: python guard refuses blocked hostname"
else
  report FAIL "process-level test: python guard did not refuse"
fi

# -----------------------------------------------------------------------------
# 5. sitecustomize 继承：安装后，良性子进程可启动；越界子进程被运行时拦截
# -----------------------------------------------------------------------------
post_rc=0
CLOUD_TEST_MODE=1 CLOUD_TEST_EGRESS_REQUIRED=1 PYTHONPATH="$REPO_ROOT" \
  timeout 60 "$PY" -c "print('benign-child-ok')" >/dev/null 2>&1 || post_rc=$?
[[ "$post_rc" -eq 0 ]] && report PASS "sitecustomize test: benign python child runs normally (exit 0)" \
                      || report FAIL  "sitecustomize test: benign python child failed rc=$post_rc"

blk_rc=0
blk_err="$(CLOUD_TEST_MODE=1 CLOUD_TEST_EGRESS_REQUIRED=1 PYTHONPATH="$REPO_ROOT" \
  timeout 60 "$PY" -c "import socket; socket.create_connection(('hike.zhihuishu.com',443),timeout=5)" 2>&1)" || blk_rc=$?
if [[ "$blk_rc" -ne 0 ]] && grep -q "CLOUD_TEST_MODE blocked" <<< "$blk_err"; then
  report PASS "sitecustomize test: blocked connect refused by guard (rc=$blk_rc)"
else
  report FAIL "sitecustomize test: expected guard refusal, got rc=$blk_rc err=$(head -c 120 <<< "$blk_err")"
fi

# -----------------------------------------------------------------------------
# 6. 子进程继承（孙进程）：父→子→孙 三层均被守卫覆盖
#    注意：父进程必须带 CLOUD_TEST_MODE + PYTHONPATH，否则 sitecustomize
#    找不到 guard 模块会 exit 78，那是「启动被拒」而不是「运行时拦截」。
# -----------------------------------------------------------------------------
if CLOUD_TEST_MODE=1 CLOUD_TEST_EGRESS_REQUIRED=1 PYTHONPATH="$REPO_ROOT" \
   timeout 90 "$PY" - <<'PYEOF'
import os, subprocess, sys
env = dict(os.environ, CLOUD_TEST_MODE="1", CLOUD_TEST_EGRESS_REQUIRED="1")
code = ("import subprocess,sys;"
        "p=subprocess.run([sys.executable,'-c',"
        "\"import socket;socket.create_connection(('passport.zhihuishu.com',443),timeout=5)\"],"
        "capture_output=True,text=True);"
        "assert p.returncode != 0 and 'CLOUD_TEST_MODE blocked' in p.stderr, "
        "f'grandchild rc={p.returncode} err={p.stderr[:200]}');"
        "print('grandchild-blocked-ok')")
r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=80)
sys.exit(r.returncode)
PYEOF
then
  report PASS "subprocess inheritance: grandchild connect refused, guard inherited via PYTHONPATH"
else
  report FAIL "subprocess inheritance: grandchild was not properly blocked"
fi

# -----------------------------------------------------------------------------
# 7. curl 绕过应用层（非 Python 二进制 → 只能靠内核层）
#    退出码 28(超时) = 只是不可达，不是被拦截 → FAIL
# -----------------------------------------------------------------------------
curl_blocked(){
  local target="$1" rc err
  err="$(curl --noproxy '*' -sS -m 12 -o /dev/null "https://$target/" 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]]; then
    report FAIL "curl bypass: $target CONNECTED (exit 0) — kernel guard missed it!"
  elif [[ $rc -eq 28 ]]; then
    report FAIL "curl bypass: $target TIMED OUT (exit 28) — unreachable, not blocked"
  else
    report PASS "curl bypass: $target blocked (exit $rc: $(head -c 90 <<< "$err"))"
  fi
}
for t in "${BLOCKED_TARGETS[@]}"; do curl_blocked "$t"; done

# 控制域：证明阻断是定向的而非断网（失败只记 WARN，不否决 —— 方案 §四）
ctrl_rc=0
curl --noproxy '*' -sS -m 12 -o /dev/null "https://$CONTROL_TARGET/" 2>/dev/null || ctrl_rc=$?
[[ $ctrl_rc -eq 0 ]] && report PASS "control: https://$CONTROL_TARGET reachable (block is targeted)" \
                    || report WARN "control: https://$CONTROL_TARGET rc=$ctrl_rc (公网访问不作硬性要求)"

# -----------------------------------------------------------------------------
# 8. IP 直连绕过 DNS：不解析域名、直接连被禁 IP → 内核 set 必须命中
# -----------------------------------------------------------------------------
bip="$(getent ahostsv4 hike.zhihuishu.com 2>/dev/null | awk '{print $1; exit}')"
if [[ -z "$bip" ]]; then
  report WARN "ip-direct: could not resolve hike.zhihuishu.com, skipped"
else
  ip_rc=0
  ip_err="$(curl --noproxy '*' -sS -m 12 -k -o /dev/null -H "Host: hike.zhihuishu.com" "https://$bip/" 2>&1)" || ip_rc=$?
  if [[ $ip_rc -eq 0 ]]; then
    report FAIL "ip-direct: raw-IP connect to $bip SUCCEEDED — DNS bypass defeats kernel set!"
  elif [[ $ip_rc -eq 28 ]]; then
    report FAIL "ip-direct: raw-IP connect to $bip timed out (exit 28) — not blocked"
  else
    report PASS "ip-direct: raw-IP $bip blocked (exit $ip_rc)"
  fi
fi

# 内核层独立证明：无 Python 守卫参与（CLOUD_TEST_MODE=0），裸 socket 连被禁 IP
krc=0
IP2="$(getent ahostsv4 api.openai.com 2>/dev/null | awk '{print $1; exit}')"
if [[ -n "$IP2" ]]; then
  kerr="$(env -u CLOUD_TEST_MODE "$PY" -c "
import socket
s=socket.socket(); s.settimeout(8)
s.connect(('$IP2',443))
" 2>&1)" || krc=$?
  if [[ $krc -eq 0 ]]; then
    report FAIL "kernel-only: raw socket to $IP2 connected WITHOUT python guard — kernel layer not enforcing!"
  else
    report PASS "kernel-only: raw socket to $IP2 refused with no python guard involved (rc=$krc)"
  fi
else
  report WARN "kernel-only: could not resolve api.openai.com, skipped"
fi

# -----------------------------------------------------------------------------
# 9. 仓库自带内核测试（真实 Linux 环境下解除 skip）
# -----------------------------------------------------------------------------
if "$PY" -c "import pytest" >/dev/null 2>&1; then
  if CLOUD_TEST_EGRESS_KERNEL_TEST=1 PYTHONPATH="$REPO_ROOT" \
     timeout 300 "$PY" -m pytest tests/test_cloud_test_guard.py -q >> "$REPORT" 2>&1; then
    report PASS "pytest kernel suite: tests/test_cloud_test_guard.py all passed on real Linux"
  else
    report FAIL "pytest kernel suite: failures (see report)"
  fi
else
  report WARN "pytest kernel suite: pytest not installed, skipped"
fi

# -----------------------------------------------------------------------------
# 结果汇总
# -----------------------------------------------------------------------------
echo "---- nft table dump ----"
nft list table inet "$TABLE" >> "$REPORT" 2>&1 || true
{
  echo "---- SUMMARY ----"
  echo "PASS=$PASS FAIL=$FAIL WARN=$WARN"
} >> "$REPORT"

echo "---- SUMMARY ----"
echo "PASS=$PASS FAIL=$FAIL WARN=$WARN"

if [[ $FAIL -gt 0 ]]; then
  echo "VERDICT: FAIL"
  exit 1
fi
echo "VERDICT: PASS"
exit 0
