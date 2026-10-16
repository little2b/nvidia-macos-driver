#ifndef NVMTL_SURFACE_KEEPALIVE_H
#define NVMTL_SURFACE_KEEPALIVE_H
#import <Foundation/Foundation.h>

// The device serializes calls. Unlike NSCache's advisory limits, both bounds
// are enforced before retaining another backing. Objects may also be held by
// applications or in-flight submissions after eviction from this cache.
@interface NVMTLSurfaceKeepalive : NSObject {
@public
    NSUInteger bytes, limit;
    NSMutableDictionary *objects, *costs;
    NSMutableOrderedSet *order;
}
- (instancetype)initWithLimit:(NSUInteger)maximum;
- (void)keep:(id)object key:(id<NSCopying>)key cost:(NSUInteger)cost;
@end
@implementation NVMTLSurfaceKeepalive
- (instancetype)initWithLimit:(NSUInteger)maximum {
    if ((self = [super init])) {
        limit = maximum; objects = [NSMutableDictionary new];
        costs = [NSMutableDictionary new]; order = [NSMutableOrderedSet new];
    }
    return self;
}
- (void)keep:(id)object key:(id<NSCopying>)key cost:(NSUInteger)cost {
    if (!object || !key || !cost || cost > limit) return;
    id old = objects[key];
    if (old == object) {
        [order removeObject:(id)key]; [order addObject:(id)key]; return;
    }
    if (old) {
        bytes -= [costs[key] unsignedIntegerValue];
        [objects removeObjectForKey:key]; [costs removeObjectForKey:key];
        [order removeObject:(id)key];
    }
    while (order.count && (bytes > limit - cost || order.count >= 64)) {
        id victim = order.firstObject;
        bytes -= [costs[victim] unsignedIntegerValue];
        [order removeObjectAtIndex:0]; [objects removeObjectForKey:victim];
        [costs removeObjectForKey:victim];
    }
    objects[key] = object; costs[key] = @(cost); [order addObject:(id)key]; bytes += cost;
}
@end
#endif
