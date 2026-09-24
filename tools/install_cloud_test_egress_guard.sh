#!/usr/bin/env bash
set -euo pipefail

TABLE="autotask_cloud_test"
MARKER="${CLOUD_TEST_EGRESS_MARKER:-/tmp/autotask-cloud-test-egress.enabled}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "cloud-test egress guard requires root/CAP_NET_ADMIN" >&2
  exit 20
fi

command -v nft >/dev/null 2>&1 || {
  echo "nft command is required; refusing to continue" >&2
  exit 21
}

HOSTS=(
  "zhihuishu.com"
  "hike.zhihuishu.com"
  "passport.zhihuishu.com"
  "chaoxing.com"
  "api.openai.com"
)

resolve_v4() {
  getent ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u
}

resolve_v6() {
  getent ahostsv6 "$1" 2>/dev/null | awk '{print $1}' | sort -u
}

V4=()
V6=()
for host in "${HOSTS[@]}"; do
  while IFS= read -r ip; do [[ -n "$ip" ]] && V4+=("$ip"); done < <(resolve_v4 "$host")
  while IFS= read -r ip; do [[ -n "$ip" ]] && V6+=("$ip"); done < <(resolve_v6 "$host")
done

if (( ${#V4[@]} == 0 && ${#V6[@]} == 0 )); then
  echo "no blocked destination IPs resolved; refusing to mark guard ready" >&2
  exit 22
fi

nft delete table inet "$TABLE" 2>/dev/null || true
nft add table inet "$TABLE"
nft 'add set inet autotask_cloud_test blocked_ipv4 { type ipv4_addr; flags interval; }'
nft 'add set inet autotask_cloud_test blocked_ipv6 { type ipv6_addr; flags interval; }'

if (( ${#V4[@]} )); then
  nft add element inet "$TABLE" blocked_ipv4 "{ $(IFS=,; echo "${V4[*]}") }"
fi
if (( ${#V6[@]} )); then
  nft add element inet "$TABLE" blocked_ipv6 "{ $(IFS=,; echo "${V6[*]}") }"
fi

nft 'add chain inet autotask_cloud_test output { type filter hook output priority 0; policy accept; }'
nft 'add rule inet autotask_cloud_test output ip daddr @blocked_ipv4 reject with icmp type port-unreachable'
nft 'add rule inet autotask_cloud_test output ip6 daddr @blocked_ipv6 reject with icmpv6 type admin-prohibited'

nft list table inet "$TABLE" >/dev/null

install -d -m 0755 "$(dirname "$MARKER")"
printf '%s\n' "enabled=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$MARKER"
chmod 0644 "$MARKER"

echo "CLOUD_TEST egress guard installed."