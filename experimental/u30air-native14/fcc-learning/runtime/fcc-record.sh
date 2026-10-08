#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Source library. A checksum detects truncated/corrupted state, not tampering.
fcc_uint() {
    case "$1" in ''|*[!0-9]*|0[0-9]*|??????????*) return 1;; esac
}
fcc_hash() {
    [ "${#1}" -eq 64 ] || return 1
    case "$1" in *[!0-9a-f]*) return 1;; esac
}
fcc_model_valid() {
    fcc_uint "$1" && fcc_uint "$2" && fcc_uint "$3" || return 1
    [ "$1" -ge 1000 ] && [ "$1" -le 6000 ] &&
    [ "$2" -ge 1000 ] && [ "$2" -le 6000 ] &&
    [ "$3" -gt 0 ] && [ "$3" -le 4096 ] &&
    [ "$(( $1 * 2 ))" -gt "$2" ] &&
    [ "$(( $1 * 10 ))" -lt "$(( $2 * 11 ))" ]
}
fcc_restore_write() {
    # sysfs parses each write separately. Shell printf can emit several writes;
    # materialize this short request before cat transfers it to the attribute.
    # attribute, learned mAh, design mAh, calibrated ADC gain
    fcc_model_valid "$2" "$3" "$4" || return 1
    local tmp rc=0
    tmp=$(mktemp /tmp/mu300-fcc-restore.XXXXXX) || return 1
    if ! chmod 600 "$tmp" || ! printf '%s %s %s\n' "$2" "$3" "$4" > "$tmp"; then
        rm -f "$tmp"; return 1
    fi
    cat "$tmp" > "$1" || rc=1
    rm -f "$tmp" || rc=1
    return "$rc"
}
fcc_record_read() {
    # file, identity hash, DTBO hash, design mAh, calibrated ADC gain
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    [ "$(wc -l < "$1")" -eq 1 ] || return 1
    [ "$(wc -c < "$1")" -lt 256 ] || return 1
    local ver ident dtbo design gain model digest extra prefix actual
    IFS='|' read -r ver ident dtbo design gain model digest extra < "$1" || return 1
    [ -z "$extra" ] && [ "$ver" = 1 ] &&
    [ "$ident" = "$2" ] && [ "$dtbo" = "$3" ] &&
    [ "$design" = "$4" ] && [ "$gain" = "$5" ] || return 1
    fcc_hash "$ident" && fcc_hash "$dtbo" && fcc_hash "$digest" || return 1
    fcc_model_valid "$model" "$design" "$gain" || return 1
    prefix="1|$ident|$dtbo|$design|$gain|$model"
    [ "$(cat "$1")" = "$prefix|$digest" ] || return 1
    actual=$(printf '%s\n' "$prefix" | sha256sum) || return 1
    [ "${actual%% *}" = "$digest" ] || return 1
    printf '%s\n' "$model"
}
fcc_record_write() {
    # file, identity hash, DTBO hash, design mAh, calibrated gain, learned mAh
    local dir tmp prefix digest
    dir=${1%/*}
    [ -d "$dir" ] && [ ! -L "$dir" ] && [ ! -L "$1" ] || return 1
    fcc_hash "$2" && fcc_hash "$3" && fcc_model_valid "$6" "$4" "$5" || return 1
    prefix="1|$2|$3|$4|$5|$6"
    digest=$(printf '%s\n' "$prefix" | sha256sum) || return 1
    tmp=$(mktemp "$dir/.fcc-record.XXXXXX") || return 1
    if ! chmod 600 "$tmp" || ! printf '%s|%s\n' "$prefix" "${digest%% *}" > "$tmp"; then
        rm -f "$tmp"; return 1
    fi
    if ! sync || ! mv -f "$tmp" "$1"; then
        rm -f "$tmp"; return 1
    fi
    sync
}
