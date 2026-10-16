#!/bin/bash
# Machine-local comparison: restore the runtime setting without editing EFI.
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin

mode=${1:---check}
case "$mode" in
  --check|--run) ;;
  *) echo "Usage: bash $0 [--check|--run]" >&2; exit 2 ;;
esac

key=debug.nvaccel_iop_flip
original=$(sysctl -n "$key")
if [ "$original" != 0 ]; then
  echo "STOP: zero-copy is already enabled or its state is unexpected ($original)." >&2
  exit 1
fi
accelerators=$(ioreg -r -c IOAccelerator -d 1 | awk '/class / {print}')
count=$(printf '%s\n' "$accelerators" | awk '/class / {n++} END {print n+0}')
if [ "$count" != 1 ] || ! printf '%s\n' "$accelerators" | grep -q '<class NVAccel,'; then
  echo "STOP: expected exactly one accelerator, NVAccel." >&2
  exit 1
fi
bars=$(ioreg -r -c NVRM -d 1 -l | grep '"nvrm-bars"')
if ! printf '%s\n' "$bars" | grep -Eq 'bar1@0x14:0x[[:xdigit:]]+\+0x200000000 '; then
  echo "STOP: expected this machine's previously inspected 8 GiB BAR1." >&2
  exit 1
fi

counters=(debug.nvaccel_iop_flip debug.nvaccel_iop_ok
  debug.nvaccel_iop_flips debug.nvaccel_iop_copy_skip
  debug.nvaccel_iop_fail debug.nvaccel_iop_flip_refused
  debug.nvaccel_iop_flip_novram debug.nvaccel_iop_flip_stale
  debug.nvaccel_iop_async_flips debug.nvaccel_iop_async_drop
  debug.nvaccel_iop_async_refused)
snapshot() {
  echo "--- $1 ($(date -u '+%Y-%m-%dT%H:%M:%SZ')) ---"
  sysctl "${counters[@]}"
}
snapshot preflight
if [ "$mode" = --check ]; then
  echo 'Preflight passed. No settings changed.'
  exit 0
fi
if [ "$EUID" != 0 ]; then
  echo 'STOP: --run needs sudo; --check does not.' >&2
  exit 1
fi

watchdog=''
cleanup() {
  rc=$?
  trap - EXIT HUP INT TERM
  if sysctl -w "$key=$original" && [ "$(sysctl -n "$key")" = "$original" ]; then
    echo "RESTORED: $key=$original; EFI and NVRAM were not edited."
    if [ -n "$watchdog" ]; then kill "$watchdog" 2>/dev/null || true; fi
  else
    echo "RESTORE FAILED: run sudo sysctl -w $key=$original" >&2
    rc=1
  fi
  exit "$rc"
}

echo 'A: 10 seconds with the current copy path. Repeatedly drag the same window.'
snapshot A-start
sleep 10
snapshot A-end

# Start a separate rollback process before touching the display path. It also
# survives termination of the foreground shell; a kernel hang cannot be covered.
nohup /bin/sh -c 'sleep 35; /usr/sbin/sysctl -w "$1=$2"' sh "$key" "$original" </dev/null >&2 &
watchdog=$!
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
echo 'B: 25 seconds with zero-copy. Repeat the same movement; watch for flicker.'
sysctl -w "$key=1"
[ "$(sysctl -n "$key")" = 1 ] || { echo 'STOP: setting did not take effect.' >&2; exit 1; }
snapshot B-start
sleep 25
snapshot B-end
echo 'Counts describe driver activity, not measured FPS. Compare smoothness and artifacts.'
