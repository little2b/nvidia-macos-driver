#!/usr/bin/env python3
"""Exercise bounded cache eviction and ownership without a GPU."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = r'''
#import "nvmtl_surface_keepalive.h"
#include <assert.h>
static void invariant(NVMTLSurfaceKeepalive *c) {
    assert(c->bytes <= c->limit && c->objects.count <= 64);
    assert(c->objects.count == c->costs.count && c->objects.count == c->order.count);
    NSUInteger sum=0;
    for(id key in c->order) { assert(c->objects[key]); sum += [c->costs[key] unsignedIntegerValue]; }
    assert(sum == c->bytes);
}
int main(void) { @autoreleasepool {
    NVMTLSurfaceKeepalive *c=[[NVMTLSurfaceKeepalive alloc] initWithLimit:100];
    __weak id a,b;
    @autoreleasepool {
        id x=[NSObject new],y=[NSObject new]; a=x; b=y;
        [c keep:x key:@"a" cost:40];[c keep:y key:@"b" cost:40];
        [c keep:x key:@"a" cost:40]; // touching a must evict b next
        [c keep:[NSObject new] key:@"c" cost:40]; invariant(c);
    }
    assert(a && !b && !c->objects[@"b"] && c->bytes==80);
    @autoreleasepool { [c keep:[NSObject new] key:@"a" cost:60]; }
    assert(!a && c->bytes==100);invariant(c);
    [c keep:[NSObject new] key:@"large" cost:101];assert(!c->objects[@"large"]);
    [c keep:[NSObject new] key:@"zero" cost:0];assert(!c->objects[@"zero"]);
    // Eviction removes only the cache's ownership, not an application's reference.
    id held=c->objects[@"a"];__weak id weakHeld=held;
    [c keep:[NSObject new] key:@"full" cost:100];assert(weakHeld && c->objects.count==1);
    held=nil;assert(!weakHeld);invariant(c);
    c=[[NVMTLSurfaceKeepalive alloc] initWithLimit:10000];
    for(unsigned i=0;i<20000;i++) { @autoreleasepool {
        [c keep:[NSObject new] key:@(i%113) cost:1+(i*37)%400];invariant(c);
    }}
    c=[[NVMTLSurfaceKeepalive alloc] initWithLimit:10000];
    for(unsigned i=0;i<100;i++) [c keep:[NSObject new] key:@(i) cost:1];
    assert(c->bytes==64 && c->objects.count==64 && !c->objects[@35] && c->objects[@36]);
    invariant(c);
    __weak id last;
    @autoreleasepool { id x=[NSObject new];last=x;[c keep:x key:@"last" cost:1]; }
    assert(last);c=nil;assert(!last);
    puts("PASS: byte/count limits, LRU touch, replacement, external ownership, 20000 evictions, teardown");
}}
'''
with tempfile.TemporaryDirectory(prefix="nvmtl-keepalive-") as temp:
    p=Path(temp); (p/'test.m').write_text(source)
    subprocess.run(['xcrun','clang','-O1','-fobjc-arc','-I',str(root/'plugin'),str(p/'test.m'),
                    '-framework','Foundation','-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True,timeout=15)
