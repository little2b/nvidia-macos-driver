#!/usr/bin/env python3
"""Exercise production shader request coalescing without a GPU or installed changes."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "plugin/NVMTLLibraryLoad.m").read_text()
helper = source[source.index("@interface NVMTLShaderFlight"):
                source.index("NSData *nvmtl_translate_with_fc_cached(")]
harness = r'''
#import <Foundation/Foundation.h>
#include <stdatomic.h>
#include <unistd.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); exit(1); } } while (0)
''' + helper + r'''
static _Atomic int calls, ready, finished, wrong;
static void concurrent(NSString *key, BOOL failure) {
    atomic_store(&calls, 0); atomic_store(&ready, 0);
    atomic_store(&finished, 0); atomic_store(&wrong, 0);
    dispatch_group_t group = dispatch_group_create();
    dispatch_semaphore_t gate = dispatch_semaphore_create(0);
    for (int i = 0; i < 8; ++i) dispatch_group_async(group, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        @autoreleasepool {
            atomic_fetch_add(&ready, 1);
            dispatch_semaphore_wait(gate, DISPATCH_TIME_FOREVER);
            NSString *error = nil;
            NSData *result = nvmtl_shader_singleflight(key, &error, ^NSData *(NSString **e) {
                atomic_fetch_add(&calls, 1);
                usleep(200000); // let all callers overlap the same pending build
                if (failure) { *e = @"translator refused input"; return nil; }
                return [@"valid shader" dataUsingEncoding:NSUTF8StringEncoding];
            });
            BOOL ok = failure ? (!result && [error isEqual:@"translator refused input"]) :
                ([result isEqual:[@"valid shader" dataUsingEncoding:NSUTF8StringEncoding]] && !error);
            if (!ok) atomic_fetch_add(&wrong, 1);
            atomic_fetch_add(&finished, 1);
        }
    });
    for (int i = 0; atomic_load(&ready) < 8 && i < 5000; ++i) usleep(1000);
    CHECK(atomic_load(&ready) == 8);
    for (int i = 0; i < 8; ++i) dispatch_semaphore_signal(gate);
    CHECK(dispatch_group_wait(group, dispatch_time(DISPATCH_TIME_NOW, 5ll * NSEC_PER_SEC)) == 0);
    CHECK(atomic_load(&calls) == (key ? 1 : 8));
    CHECK(atomic_load(&wrong) == 0 && atomic_load(&finished) == 8);
}
int main(void) { @autoreleasepool {
    concurrent(@"same", NO);
    concurrent(@"same", YES); // completed flights must not retain stale results
    concurrent(@"same", NO);  // failures must be retryable
    concurrent(nil, NO);      // disabled cache preserves independent builds
    dispatch_group_t workers = dispatch_group_create();
    dispatch_semaphore_t both = dispatch_semaphore_create(0);
    for (int i = 0; i < 2; ++i) dispatch_group_async(workers, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        @autoreleasepool {
            nvmtl_shader_singleflight([NSString stringWithFormat:@"distinct-%d", i], NULL, ^NSData *(NSString **e) {
                dispatch_semaphore_signal(both);
                CHECK(dispatch_semaphore_wait(both, dispatch_time(DISPATCH_TIME_NOW, 2ll * NSEC_PER_SEC)) == 0);
                // A shared barrier below checks both builders can be active together.
                atomic_fetch_add(&ready, 1);
                for (int j = 0; atomic_load(&ready) < 10 && j < 2000; ++j) usleep(1000);
                CHECK(atomic_load(&ready) == 10);
                return [NSData data];
            });
        }
    });
    CHECK(dispatch_group_wait(workers, dispatch_time(DISPATCH_TIME_NOW, 5ll * NSEC_PER_SEC)) == 0);
    @try {
        nvmtl_shader_singleflight(@"exception", NULL, ^NSData *(NSString **e) {
            @throw [NSException exceptionWithName:@"test" reason:@"failure" userInfo:nil];
        });
        CHECK(NO);
    } @catch (NSException *e) { CHECK([e.name isEqual:@"test"]); }
    CHECK(nvmtl_shader_singleflight(@"exception", NULL, ^NSData *(NSString **e) { return [NSData data]; }) != nil);
    puts("PASS: duplicate requests, failure fan-out/retry, cache bypass, independent keys, exception cleanup");
    return 0;
}}
'''
with tempfile.TemporaryDirectory(prefix="nvmtl-singleflight-") as temporary:
    directory = Path(temporary)
    (directory / "probe.m").write_text(harness)
    subprocess.run(["xcrun", "clang", "-fobjc-arc", "-O1", str(directory / "probe.m"),
                    "-framework", "Foundation", "-o", str(directory / "probe")], check=True)
    subprocess.run([str(directory / "probe")], check=True, timeout=15)
