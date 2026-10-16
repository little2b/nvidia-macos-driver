#!/usr/bin/env python3
"""Compare bounded cross-frame import caching in isolated GPU processes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("plugin", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--rounds", type=int, default=3)
parser.add_argument("--width", type=int, default=3840)
args = parser.parse_args()
if not 1 <= args.rounds <= 20:
    parser.error("rounds must be 1..20")
if not 1024 <= args.width <= 4096:
    parser.error("width must be 1024..4096")
plugin = args.plugin.resolve(strict=True)
args.output.mkdir(parents=True, exist_ok=False)
env = {k:v for k,v in os.environ.items() if not k.startswith(("DYLD_", "NM_TEST_", "GPU_PROBE_"))}
def windowserver():
    return subprocess.check_output(["pgrep", "-x", "WindowServer"],text=True).strip()
initial = windowserver()
runs = []
with tempfile.TemporaryDirectory(prefix="nvmtl-churn-") as temporary:
    binary = str(Path(temporary)/"probe")
    subprocess.run(["xcrun","clang","-O1","-fobjc-arc",str(Path(__file__).with_suffix(".m")),
                    "-framework","Foundation","-framework","Metal","-framework","IOSurface",
                    "-framework","IOKit","-o",binary],check=True,env=env)
    def probe(name, mb, surfaces):
        if windowserver() != initial:
            raise RuntimeError("WindowServer restarted")
        try:
            p = subprocess.run([binary,str(plugin),str(mb),str(surfaces),str(args.width)],capture_output=True,
                               text=True,env=env,timeout=55)
        except subprocess.TimeoutExpired as error:
            (args.output/(name+".timeout.txt")).write_text(str(error))
            raise
        (args.output/(name+".stderr.txt")).write_text(p.stderr)
        (args.output/(name+".json")).write_text(p.stdout)
        if p.returncode or "implemented in both" in p.stderr or windowserver() != initial:
            raise RuntimeError("Probe failed: "+name)
        r = json.loads(p.stdout)
        assert r["verified"] and r["verified_frames"] == 96
        assert r["plugin"] == str(plugin) and r["cache_MiB"] == mb and r["surfaces"] == surfaces
        assert r["width"] == args.width
        print(name+": verified",flush=True)
        return r
    for i in range(args.rounds):
        for mb in ((0,128) if i%2 == 0 else (128,0)):
            runs.append(probe(f"round-{i}-cache-{mb}",mb,3))
    probe("eviction-128-6",128,6)
    probe("oversize-1-3",1,3)
summary = {}
for mb in (0,128):
    rows = [r for r in runs if r["cache_MiB"] == mb]
    summary[mb] = {"median_create_ms":statistics.median(statistics.median(r["create_ms"]) for r in rows),
                   "median_frame_ms":statistics.median(statistics.median(r["frame_ms"]) for r in rows),
                   "buffer_allocations":[r["buffer_allocations"] for r in rows]}
result = {"scope":"Offscreen CPU-update/render/readback workload, not desktop FPS", "summary":summary,
          "plugin":str(plugin),"sha256":hashlib.sha256(plugin.read_bytes()).hexdigest(),"rounds":args.rounds,"width":args.width}
(args.output/"summary.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps(summary,indent=2))
