#!/bin/sh
# Fail-closed guard for the U30 Air native kernel capability provider.
# It verifies the running target and records evidence; it never changes packages.
set -u

EXPECTED_KREL='7.2.9-u30air-charge2'
EXPECTED_UNAME_ARCH='aarch64'
EXPECTED_APK_ARCH='aarch64_generic'
EXPECTED_VERMAGIC='7.2.9-u30air-charge2 SMP preempt mod_unload aarch64'
EXPECTED_KERNEL_ARCHIVE_SHA256='fd87b29a0720331447d9b9fb2cad044cb8b190cc5b3c3c5670a513af039aa71e'
EXPECTED_EFFECTIVE_CONFIG_SHA256='7f87982af61c7a9a5577babac21b3055042fcba513c89dae95138a56afe6795e'
EXPECTED_BUILTINS_SHA256='9f37862d34d1f5cd1596fc9d718e257dd3f2174b31f81cb8447096cd7f940d74'
MODULE_ROOT="/lib/modules/$EXPECTED_KREL"
EVIDENCE_FILE="${MU300_NATIVE_KMOD_EVIDENCE:-/tmp/mu300-native-kmod-compat-guard.log}"

umask 077
if ! : >> "$EVIDENCE_FILE"; then
  printf '%s\n' "FAIL: cannot write evidence file: $EVIDENCE_FILE" >&2
  exit 1
fi
for tool in uname gzip awk sha256sum grep tee strings; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf '%s\n' "FAIL: required command is unavailable: $tool" | tee -a "$EVIDENCE_FILE" >&2
    exit 1
  }
done
log() { printf '%s\n' "$*" | tee -a "$EVIDENCE_FILE"; }
fail() { log "FAIL: $*"; exit 1; }

log 'mu300-native-kmod-compat preflight'
log "timestamp_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf unknown)"
log "expected_kernel_release=$EXPECTED_KREL"
log "expected_uname_arch=$EXPECTED_UNAME_ARCH"
log "expected_apk_arch=$EXPECTED_APK_ARCH"
log "frozen_kernel_archive_sha256=$EXPECTED_KERNEL_ARCHIVE_SHA256 (provenance; not a runtime image hash)"
log "frozen_effective_config_sha256=$EXPECTED_EFFECTIVE_CONFIG_SHA256 (critical symbols checked below)"

actual_krel=$(uname -r) || fail 'cannot read uname -r'
[ "$actual_krel" = "$EXPECTED_KREL" ] || fail "uname -r is $actual_krel"
log "uname_r=$actual_krel"
actual_arch=$(uname -m) || fail 'cannot read uname -m'
[ "$actual_arch" = "$EXPECTED_UNAME_ARCH" ] || fail "uname -m is $actual_arch"
log "uname_m=$actual_arch"

CONFIG_GZ=/proc/config.gz
[ -r "$CONFIG_GZ" ] || fail 'missing readable /proc/config.gz'
check_config() {
  symbol=$1
  expected=$2
  actual=$(gzip -dc "$CONFIG_GZ" 2>/dev/null | awk -F= -v key="CONFIG_$symbol" '
    $1 == key { if (found) { bad=1; exit }; found=1; value=$2 }
    $0 == "# " key " is not set" { if (found) { bad=1; exit }; found=1; value="n" }
    END { if (bad || found != 1) exit 1; print value }
  ') || fail "cannot read a unique CONFIG_$symbol from /proc/config.gz"
  [ "$actual" = "$expected" ] || fail "CONFIG_$symbol is $actual, expected $expected"
  log "CONFIG_$symbol=$actual"
}

check_config DUMMY m
check_config INET_DIAG y
check_config INET_TCP_DIAG y
check_config INET_UDP_DIAG m
check_config INET_RAW_DIAG m
check_config NFT_SOCKET y
check_config NFT_TPROXY y
check_config NF_SOCKET_IPV4 y
check_config NF_SOCKET_IPV6 y
check_config NF_TPROXY_IPV4 y
check_config NF_TPROXY_IPV6 y

[ -d "$MODULE_ROOT" ] || fail "missing module directory: $MODULE_ROOT"
verify_module() {
  name=$1
  expected_sha256=$2
  module="$MODULE_ROOT/$name.ko"
  [ -r "$module" ] || fail "missing readable module: $module"
  hash_line=$(sha256sum "$module") || fail "cannot hash $module"
  actual_sha256=${hash_line%% *}
  [ "$actual_sha256" = "$expected_sha256" ] || fail "$name.ko SHA256 mismatch: $actual_sha256"
  # Prefer modinfo where its -F option is supported. Some target modinfo applets
  # reject -F, so fall back to the unique vermagic= ELF string. Command
  # substitution removes trailing newlines only; it does not trim field spaces.
  actual_vermagic=
  if command -v modinfo >/dev/null 2>&1; then
    if actual_vermagic=$(modinfo -F vermagic "$module" 2>/dev/null) && [ -n "$actual_vermagic" ]; then
      :
    else
      actual_vermagic=
    fi
  fi
  if [ -z "$actual_vermagic" ]; then
    strings_output=$(strings "$module" 2>/dev/null) || fail "cannot extract strings from $name.ko"
    actual_vermagic=$(printf '%s\n' "$strings_output" | awk '
      /^vermagic=/ { count++; value=substr($0, 10) }
      END { if (count != 1 || value == "") exit 1; print value }
    ') || fail "$name.ko does not contain exactly one non-empty vermagic= string"
  fi
  [ "$actual_vermagic" = "$EXPECTED_VERMAGIC" ] || fail "$name.ko vermagic mismatch: $actual_vermagic"
  log "module=$name.ko sha256=$actual_sha256 vermagic=$actual_vermagic"
}

# These exact module hashes and vermagic values were extracted from the frozen 7.2.9 kernel.
verify_module dummy 13dceaac167531650262f8d782b4e79df9a93cd6c8efd45dfc7bcbba0690ccce
verify_module udp_diag 81b6171e6c8b3ca081b08f83a9df50d87ac7a672b03667ee5bb93445ff414aa4
verify_module raw_diag 259fb82e25c352cfff6dadeabd80f3d61b74d08fa5d977a9efd903b594a775c1

BUILTINS="$MODULE_ROOT/modules.builtin"
[ -r "$BUILTINS" ] || fail "missing readable builtin list: $BUILTINS"
builtin_hash_line=$(sha256sum "$BUILTINS") || fail 'cannot hash modules.builtin'
actual_builtin_sha256=${builtin_hash_line%% *}
[ "$actual_builtin_sha256" = "$EXPECTED_BUILTINS_SHA256" ] || fail "modules.builtin SHA256 mismatch: $actual_builtin_sha256"
log "modules_builtin_sha256=$actual_builtin_sha256"
check_builtin() {
  entry=$1
  grep -Fxq "$entry" "$BUILTINS" || fail "missing builtin entry: $entry"
  log "builtin=$entry"
}
check_builtin kernel/net/netfilter/nft_socket.ko
check_builtin kernel/net/netfilter/nft_tproxy.ko
check_builtin kernel/net/ipv4/inet_diag.ko
check_builtin kernel/net/ipv4/tcp_diag.ko
check_builtin kernel/net/ipv4/netfilter/nf_socket_ipv4.ko
check_builtin kernel/net/ipv4/netfilter/nf_tproxy_ipv4.ko
check_builtin kernel/net/ipv6/netfilter/nf_socket_ipv6.ko
check_builtin kernel/net/ipv6/netfilter/nf_tproxy_ipv6.ko

log 'PASS: exact 7.2.9 native capability guard'
exit 0
