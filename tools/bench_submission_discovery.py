#!/usr/bin/env python3
"""Compare resource classification only, not GPU time or desktop FPS."""
import argparse
import ast
import json
from pathlib import Path
import statistics
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
parser.add_argument("--baseline", default="b737dedc11fa4edbcb4abe8d51e94cebfed21ac6")
args = parser.parse_args()
baseline = subprocess.check_output(
    ["git", "show", args.baseline + ":plugin/NVMTLEventSync.m"], cwd=root, text=True)
start = baseline.index("static NSArray<NVMTLBuffer *> *nvmtl_submission_copies(")
old = baseline[start:baseline.index("- (BOOL)nvmtlSubmitAhead", start)]
old = old.replace("nvmtl_prepare_submission_copies", "baseline_prepare")
source = (root / "plugin/NVMTLEventSync.m").read_text()
start = source.index("static void nvmtl_prepare_submission_copies(")
new = source[start:source.index("- (BOOL)nvmtlSubmitAhead", start)]
tree = ast.parse((root / "tools/test_submission_copies.py").read_text())
fixture = next(ast.literal_eval(node.value) for node in tree.body
               if isinstance(node, ast.Assign) and any(
                   isinstance(t, ast.Name) and t.id == "fixture" for t in node.targets))
bench = r'''
#include <time.h>
static volatile NSUInteger sink;
static NVMTLSubmissionPart *run(NSSet *resources, BOOL candidate) {
    NVMTLSubmissionPart *p=[NVMTLSubmissionPart new]; p->resources=resources;
    if(candidate) nvmtl_prepare_submission_copies(p);
    else { baseline_prepare(p); p->copyOutputs=nvmtl_submission_copies(resources,YES); }
    sink+=p->copyInputs.count+p->copyOutputs.count+p->hasHostCopies;
    return p;
}
static double timing(NSSet *r, BOOL candidate, unsigned iterations) {
    uint64_t start=clock_gettime_nsec_np(CLOCK_MONOTONIC);
    for(unsigned i=0;i<iterations;i++) {@autoreleasepool{ (void)run(r,candidate); }}
    return (double)(clock_gettime_nsec_np(CLOCK_MONOTONIC)-start)/iterations;
}
int main(void) {@autoreleasepool {
    NSMutableArray *results=[NSMutableArray new];
    for(NSNumber *size in @[@0,@32,@256,@1024]) for(NSNumber *mixed in @[@NO,@YES]) {
        NSMutableSet *resources=[NSMutableSet new]; unsigned count=size.unsignedIntValue;
        for(unsigned i=0;i<count;i++) {
            NVMTLTexture *t=[NVMTLTexture new]; t->_usage=MTLTextureUsageShaderRead;
            if(mixed.boolValue && !(i%8)) {
                NVMTLBuffer *b=[NVMTLBuffer new]; b->_hostPtr=(void *)1; t->_backBuf=b;
                NVMTLTexture *view=[NVMTLTexture new];view->_parent=t;
                view->_usage=(i%16) ? MTLTextureUsageShaderRead : MTLTextureUsageShaderWrite;
                [resources addObject:view];
            }
            [resources addObject:t];
        }
        NVMTLSubmissionPart *a=run(resources,NO),*b=run(resources,YES);
        assert([[NSSet setWithArray:a->copyInputs?:@[]] isEqualToSet:[NSSet setWithArray:b->copyInputs?:@[]]]);
        assert([[NSSet setWithArray:a->copyOutputs?:@[]] isEqualToSet:[NSSet setWithArray:b->copyOutputs?:@[]]]);
        assert(a->hasHostCopies==b->hasHostCopies);
        unsigned iterations=count>256 ? 250 : 1000;
        (void)timing(resources,NO,100); (void)timing(resources,YES,100);
        NSMutableArray *oldTimes=[NSMutableArray new],*newTimes=[NSMutableArray new];
        for(unsigned round=0;round<11;round++) {
            double oldNs,newNs;
            if(round%2) { newNs=timing(resources,YES,iterations);oldNs=timing(resources,NO,iterations); }
            else { oldNs=timing(resources,NO,iterations);newNs=timing(resources,YES,iterations); }
            [oldTimes addObject:@(oldNs)];[newTimes addObject:@(newNs)];
        }
        [results addObject:@{@"resources":@(resources.count),@"mixed_copy_backings":mixed,
            @"baseline_ns":oldTimes,@"candidate_ns":newTimes,@"sets_verified":@YES}];
    }
    NSData *data=[NSJSONSerialization dataWithJSONObject:results options:0 error:nil];
    fwrite(data.bytes,1,data.length,stdout);
}}
'''
with tempfile.TemporaryDirectory(prefix="nvmtl-discovery-") as temp:
    path = Path(temp)
    (path / "bench.m").write_text(fixture + old + new + bench)
    subprocess.run(["xcrun", "clang", "-O1", "-fobjc-arc", str(path / "bench.m"),
                    "-framework", "Foundation", "-framework", "Metal", "-o", str(path / "bench")], check=True)
    rows = json.loads(subprocess.check_output([str(path / "bench")], text=True, timeout=55))
for row in rows:
    old_ns = statistics.median(row["baseline_ns"])
    new_ns = statistics.median(row["candidate_ns"])
    row.update(baseline_median_ns=old_ns, candidate_median_ns=new_ns,
               reduction_percent=100*(old_ns-new_ns)/old_ns)
result = {"scope": "CPU resource discovery microbenchmark; not GPU or desktop FPS",
          "baseline": args.baseline, "rounds": 11, "results": rows}
with args.output.open("x") as f:
    json.dump(result, f, indent=2)
    f.write("\n")
for row in rows:
    print(f"{row['resources']:4} resources, mixed={row['mixed_copy_backings']}: "
          f"{row['baseline_median_ns']/1000:.2f} -> {row['candidate_median_ns']/1000:.2f} us "
          f"({row['reduction_percent']:.1f}% less classification time)")
