#!/usr/bin/env python3
"""Check an isolated plugin's copied buffers and IOSurface ordering on the GPU."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("plugin", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--surface-cache-mb", type=int, choices=range(257), metavar="0..256")
args = parser.parse_args()
plugin = args.plugin.resolve(strict=True)
args.output.mkdir(parents=True, exist_ok=False)
root = Path(__file__).resolve().parents[1]
env = {k: v for k, v in os.environ.items() if not k.startswith(("DYLD_", "NM_TEST_", "GPU_PROBE_"))}
env["NM_TEST_PLUGIN"] = str(plugin)
if args.surface_cache_mb is not None:
    env["NM_TEST_SURFACE_CACHE_MB"] = str(args.surface_cache_mb)
def windowserver():
    return subprocess.check_output(["pgrep", "-x", "WindowServer"], text=True).strip()
initial = windowserver()
results = []
with tempfile.TemporaryDirectory(prefix="nvmtl-hardware-") as temp:
    temp = Path(temp)
    for name in ("bench_copy_coherence", "bench_surface_order", "bench_surface_wrappers"):
        subprocess.run(["xcrun", "clang", "-O1", "-fobjc-arc", str(root / "tools" / (name + ".m")),
                        "-framework", "Foundation", "-framework", "Metal", "-framework", "IOKit",
                        "-framework", "IOSurface", "-o", str(temp / name)], check=True, env=env)
    cases = [("copied-buffers", [str(temp / "bench_copy_coherence"), str(plugin)],
              "RESULT frames=16 host_to_texture_bad=0 texture_to_host_bad=0"),
             ("compute-wrappers", [str(temp / "bench_surface_wrappers")], "OK (0 of 12 cases wrong)")]
    for mode in ("same", "queued", "wait", "alias", "alias_read", "partial", "partial_alias", "cpu", "abandoned", "cross"):
        cases.append((mode, [str(temp / "bench_surface_order"), mode], f"RESULT mode={mode} frames=100 bad=0"))
    for label, cmd, expected in cases:
        if windowserver() != initial:
            raise RuntimeError("WindowServer restarted; stopping")
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=55, env=env)
        except subprocess.TimeoutExpired as exc:
            (args.output / (label + ".timeout.txt")).write_text(str(exc))
            raise
        (args.output / (label + ".stdout.txt")).write_text(run.stdout)
        (args.output / (label + ".stderr.txt")).write_text(run.stderr)
        passed = (run.returncode == 0 and expected in run.stdout
                  and "implemented in both" not in run.stderr and windowserver() == initial)
        results.append({"test": label, "passed": passed, "exit_code": run.returncode})
        (args.output / "results.json").write_text(json.dumps({
            "plugin": str(plugin), "sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
            "windowserver_pid": initial, "surface_cache_mb": args.surface_cache_mb,
            "tests": results}, indent=2) + "\n")
        if not passed:
            raise RuntimeError("Failed " + label + "; see saved logs")
        print(label + ": passed", flush=True)
