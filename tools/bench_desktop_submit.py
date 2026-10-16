#!/usr/bin/env python3
"""Alternate isolated plugins on a verified offscreen 4K IOSurface workload."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("baseline", type=Path)
parser.add_argument("candidate", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--rounds", type=int, default=5)
args = parser.parse_args()
if not 1 <= args.rounds <= 20:
    parser.error("rounds must be 1..20")
plugins = {label: str(getattr(args, label).resolve(strict=True)) for label in ("baseline", "candidate")}
args.output.mkdir(parents=True, exist_ok=False)
env = {k: v for k, v in os.environ.items() if not k.startswith(("DYLD_", "NM_TEST_", "GPU_PROBE_"))}
def windowserver():
    return subprocess.check_output(["pgrep", "-x", "WindowServer"], text=True).strip()
initial = windowserver()
runs = []
with tempfile.TemporaryDirectory(prefix="nvmtl-submit-") as temp:
    binary = str(Path(temp) / "probe")
    subprocess.run(["xcrun", "clang", "-O1", "-fobjc-arc", "-Wno-deprecated-declarations",
                    str(Path(__file__).with_suffix(".m")), "-framework", "Foundation",
                    "-framework", "Metal", "-framework", "IOKit", "-framework", "IOSurface",
                    "-o", binary], check=True, env=env)
    for resources in (32, 256):
        for iteration in range(args.rounds):
            order = ("baseline", "candidate") if iteration % 2 == 0 else ("candidate", "baseline")
            for label in order:
                name = f"{resources}-{iteration + 1}-{label}"
                if windowserver() != initial:
                    raise RuntimeError("WindowServer restarted; stopping")
                try:
                    run = subprocess.run([binary, plugins[label], str(resources)], env=env,
                                         capture_output=True, text=True, timeout=55)
                except subprocess.TimeoutExpired as exc:
                    (args.output / (name + ".timeout.txt")).write_text(str(exc))
                    raise
                (args.output / (name + ".stderr.txt")).write_text(run.stderr)
                if run.returncode or "implemented in both" in run.stderr or windowserver() != initial:
                    (args.output / (name + ".stdout.txt")).write_text(run.stdout)
                    raise RuntimeError("Probe failed: " + name)
                result = json.loads(run.stdout)
                assert result["verified"] is True and result["plugin"] == plugins[label]
                for key in ("wall_ms", "cpu_ms", "commit_wait_ms"):
                    assert len(result[key]) == 180 and all(math.isfinite(v) and v >= 0 for v in result[key])
                result.update(label=label, round=iteration + 1)
                (args.output / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")
                runs.append(result)
                print(name + ": verified", flush=True)
summary = {}
for resources in (32, 256):
    summary[resources] = {}
    for label in plugins:
        selected = [r for r in runs if r["resources"] == resources and r["label"] == label]
        summary[resources][label] = {
            "median_wall_ms": statistics.median(statistics.median(r["wall_ms"]) for r in selected),
            "median_cpu_ms": statistics.median(statistics.median(r["cpu_ms"]) for r in selected),
            "median_p95_wall_ms": statistics.median(sorted(r["wall_ms"])[math.ceil(.95*len(r["wall_ms"]))-1] for r in selected),
        }
result = {"scope": "offscreen submission; not desktop FPS or scan-out latency", "rounds": args.rounds,
          "plugins": plugins, "sha256": {k: hashlib.sha256(Path(v).read_bytes()).hexdigest() for k, v in plugins.items()},
          "windowserver_unchanged": windowserver() == initial, "summary": summary}
(args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(summary, indent=2))
