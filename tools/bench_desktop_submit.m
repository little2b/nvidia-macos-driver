// Offscreen 4K IOSurface submission latency, not WindowServer FPS or scan-out.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <sys/resource.h>
#include <time.h>

static double wall(void) { return clock_gettime_nsec_np(CLOCK_MONOTONIC)/1e6; }
static double cpu(void) {
    struct rusage r; getrusage(RUSAGE_SELF,&r);
    return (r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000.0+(r.ru_utime.tv_usec+r.ru_stime.tv_usec)/1000.0;
}
static void fail(const char *s) { fprintf(stderr,"PROBE_ERROR: %s\n",s); exit(2); }
int main(int argc,char **argv) { @autoreleasepool {
    if(argc!=3) fail("usage: probe /absolute/plugin/path resource-count");
    char *end=NULL; long count=strtol(argv[2],&end,10);
    if(!end || *end || count<0 || count>1024) fail("resource-count must be 0..1024");
    if(!dlopen(argv[1],RTLD_NOW|RTLD_LOCAL)) fail(dlerror());
    io_service_t svc=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    if(!svc) fail("NVAccel unavailable");
    id<MTLDevice> d=((id(*)(id,SEL,mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc],NSSelectorFromString(@"initWithAcceleratorPort:"),svc);
    IOObjectRelease(svc);
    if(!d || strcmp(class_getImageName(object_getClass(d)),argv[1])) fail("wrong device/plugin");
    id<MTLCommandQueue> q=[d newCommandQueue];
    id<MTLBuffer> pixel=[d newBufferWithLength:256 options:MTLResourceStorageModeShared];
    if(!q || !pixel) fail("queue/readback allocation");
    NSMutableArray *resources=[NSMutableArray new],*textures=[NSMutableArray new];
    for(long i=0;i<count;i++) {
        id<MTLBuffer> b=[d newBufferWithLength:4096 options:MTLResourceStorageModePrivate];
        if(!b) fail("resource allocation"); [resources addObject:b];
    }
    const NSUInteger width=3840,height=2160;
    IOSurfaceRef surfaces[3]={0};
    for(unsigned i=0;i<3;i++) {
        surfaces[i]=IOSurfaceCreate((__bridge CFDictionaryRef)@{
            (id)kIOSurfaceWidth:@(width),(id)kIOSurfaceHeight:@(height),
            (id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
        if(!surfaces[i]) fail("IOSurface allocation");
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:width height:height mipmapped:NO];
        td.storageMode=MTLStorageModeShared; td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> t=[d newTextureWithDescriptor:td iosurface:surfaces[i] plane:0];
        if(!t) fail("IOSurface texture allocation"); [textures addObject:t];
    }
    NSMutableArray *elapsed=[NSMutableArray new],*cpuTimes=[NSMutableArray new],*commitTimes=[NSMutableArray new];
    for(unsigned frame=0;frame<212;frame++) { @autoreleasepool {
        double start=wall(),cpuStart=cpu();
        id<MTLTexture> t=textures[frame%3];
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture=t; rp.colorAttachments[0].loadAction=MTLLoadActionClear;
        rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        rp.colorAttachments[0].clearColor=MTLClearColorMake(frame%2,0,1-frame%2,1);
        id<MTLCommandBuffer> c=[q commandBuffer];
        id<MTLRenderCommandEncoder> render=[c renderCommandEncoderWithDescriptor:rp];
        if(!c || !render) fail("render encoder allocation");
        for(id<MTLResource> r in resources) [render useResource:r usage:MTLResourceUsageRead];
        [render endEncoding];
        id<MTLBlitCommandEncoder> blit=[c blitCommandEncoder];
        if(!blit) fail("blit encoder allocation");
        [blit copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(frame%width,frame%height,0)
            sourceSize:MTLSizeMake(1,1,1) toBuffer:pixel destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256];
        [blit endEncoding];
        double commitStart=wall(); [c commit]; [c waitUntilCompleted];
        double finish=wall(),cpuEnd=cpu();
        if(c.status!=MTLCommandBufferStatusCompleted) fail(c.error.description.UTF8String?:"GPU command failed");
        const uint8_t *p=pixel.contents;
        if(p[0]!=(frame%2 ? 0 : 255) || p[1] || p[2]!=(frame%2 ? 255 : 0) || p[3]!=255) fail("rendered pixel mismatch");
        if(frame>=32) { [elapsed addObject:@(finish-start)];[cpuTimes addObject:@(cpuEnd-cpuStart)];[commitTimes addObject:@(finish-commitStart)]; }
    }}
    NSDictionary *result=@{@"scope":@"offscreen 4K clear + one-pixel verification; no display presentation",
        @"plugin":@(argv[1]),@"device":d.name,@"resources":@(count),@"width":@(width),@"height":@(height),
        @"warmup":@32,@"samples":@180,@"verified":@YES,@"wall_ms":elapsed,@"cpu_ms":cpuTimes,@"commit_wait_ms":commitTimes};
    NSData *json=[NSJSONSerialization dataWithJSONObject:result options:0 error:nil];
    fwrite(json.bytes,1,json.length,stdout);fputc('\n',stdout);
    for(unsigned i=0;i<3;i++) CFRelease(surfaces[i]);
}}
