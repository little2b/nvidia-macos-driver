// Isolated AIR translation probe. Does not create a Metal device or install a driver.
// clang -fobjc-arc -O2 tools/bench_shader_cache.m -framework Foundation -o /tmp/bench_shader_cache
// bench_shader_cache PLUGIN AIR_FILE NEW_CACHE_DIRECTORY THREADS [BOOL_VALUE]
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#include <dlfcn.h>
#include <sys/resource.h>
#include <time.h>

typedef NSData *(*Translate)(NSString *, NSString *, NSString *, NSDictionary *,
                             const uint32_t *, const uint32_t *, const uint8_t *const *, size_t, NSString **);
static double wall(void) { return clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW) / 1e9; }
static double cpu(void) {
    struct rusage r; getrusage(RUSAGE_SELF, &r);
    return r.ru_utime.tv_sec + r.ru_stime.tv_sec + (r.ru_utime.tv_usec + r.ru_stime.tv_usec) / 1e6;
}
int main(int argc, char **argv) { @autoreleasepool {
    if (argc < 5) return 2;
    const int threads = atoi(argv[4]);
    if (threads < 1 || threads > 16) return 2;
    void *handle = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!handle) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    Translate translate = (Translate)dlsym(handle, "nvmtl_translate_with_fc_cached");
    if (!translate) return 1;
    // Release plugin initialization clears tuning variables, so set the private cache afterwards.
    setenv("NVMTL_AIRCACHE", [[[NSString stringWithUTF8String:argv[3]] stringByAppendingPathComponent:@"aircache"] fileSystemRepresentation], 1);
    NSString *air = [NSString stringWithContentsOfFile:@(argv[2]) encoding:NSUTF8StringEncoding error:NULL];
    if (!air) return 1;
    const uint32_t index = 61, size = 1;
    const uint8_t value = argc > 5 ? atoi(argv[5]) : 0;
    const uint8_t *payload = &value;
    NSDictionary *constants = @{@"61": @[@(value)]};
    NSMutableArray *results = [NSMutableArray new], *errors = [NSMutableArray new];
    dispatch_group_t workers = dispatch_group_create();
    dispatch_semaphore_t start = dispatch_semaphore_create(0);
    double t = wall(), c = cpu();
    for (int i = 0; i < threads; ++i) {
        dispatch_group_async(workers, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{ @autoreleasepool {
            dispatch_semaphore_wait(start, DISPATCH_TIME_FOREVER);
            NSString *error = nil;
            NSData *result = translate(air, @"fragment", @"fixed_frag_lpf_cpf", constants, &index, &size, &payload, 1, &error);
            @synchronized(results) {
                if (result) [results addObject:result];
                else [errors addObject:error ?: @"no result"];
            }
        }});
    }
    for (int i = 0; i < threads; ++i) dispatch_semaphore_signal(start);
    if (dispatch_group_wait(workers, dispatch_time(DISPATCH_TIME_NOW, 45ll * NSEC_PER_SEC))) return 1;
    double elapsed = (wall() - t) * 1000, cpuMs = (cpu() - c) * 1000;
    if (errors.count || results.count != threads) { fprintf(stderr, "%s\n", errors.description.UTF8String); return 1; }
    NSData *expected = results[0];
    for (NSData *result in results) if (![result isEqualToData:expected]) return 1;
    t = wall();
    for (int i = 0; i < 100; ++i) {
        NSData *result = translate(air, @"fragment", @"fixed_frag_lpf_cpf", constants, &index, &size, &payload, 1, NULL);
        if (![result isEqualToData:expected]) return 1;
    }
    double warm = (wall() - t) * 10;
    uint8_t hash[CC_SHA256_DIGEST_LENGTH]; CC_SHA256(expected.bytes, (CC_LONG)expected.length, hash);
    NSMutableString *hex = [NSMutableString new];
    for (int i = 0; i < sizeof hash; ++i) [hex appendFormat:@"%02x", hash[i]];
    NSDictionary *report = @{@"threads": @(threads), @"wall_ms": @(elapsed), @"cpu_ms": @(cpuMs),
        @"warm_mean_ms": @(warm), @"bytes": @(expected.length), @"sha256": hex, @"verified": @YES};
    puts([[NSString alloc] initWithData:[NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:NULL] encoding:NSUTF8StringEncoding].UTF8String);
    return 0;
}}
