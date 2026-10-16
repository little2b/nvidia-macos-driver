#!/usr/bin/env python3
"""Read presentation counters; activity rates are not measured monitor FPS."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
parser.add_argument("--seconds", type=int, default=20)
args = parser.parse_args()
if not 1 <= args.seconds <= 60:
    parser.error("seconds must be 1..60")
keys = ["debug." + name for name in (
    "nvaccel_iop_flip", "nvaccel_iop_ok", "nvaccel_iop_flips", "nvaccel_iop_copy_skip",
    "nvaccel_iop_fail", "nvaccel_iop_flip_lock", "nvaccel_iop_flip_refused",
    "nvaccel_iop_async_drop", "nvrmfb_flip_n", "nvrmfb_flip_us_sum",
    "nvrmfb_flip_latch_timeouts", "nvrmfb_vram_mapped_bytes", "nvaccel_vm_refused")]
samples = []
start = time.monotonic()
for i in range(args.seconds * 2 + 1):
    remaining = start + i * .5 - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    lines = subprocess.check_output(["sysctl", *keys], text=True, timeout=3).splitlines()
    values = {k: int(v) for k, v in (line.split(": ", 1) for line in lines)}
    samples.append({"seconds": time.monotonic() - start, "values": values})
delta = {k: samples[-1]["values"][k] - samples[0]["values"][k] for k in keys}
n = delta["debug.nvrmfb_flip_n"]
result = {"captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "samples": samples, "last_minus_first": delta,
          "mean_flip_call_us": delta["debug.nvrmfb_flip_us_sum"] / n if n > 0 else None,
          "note": "Cumulative counters are not FPS. Settings and live gauges also appear in last_minus_first."}
with args.output.open("x") as f:
    json.dump(result, f, indent=2)
    f.write("\n")
print(json.dumps({k: v for k, v in result.items() if k != "samples"}, indent=2))
