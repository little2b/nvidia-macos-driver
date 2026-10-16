// Measure repeated wrappers of one live surface, including import allocation counts.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <time.h>
static void fail(const char *s) { fprintf(stderr,"PROBE_ERROR: %s\n",s);exit(2); }
static double now(void) { return clock_gettime_nsec_np(CLOCK_MONOTONIC)/1e6; }
typedef void (*Counts)(uint64_t *,uint64_t *,uint64_t *,uint64_t *,size_t *);
int main(int argc,char **argv) {@autoreleasepool {
    if(argc!=2) fail("usage: probe /absolute/plugin");
    void *plugin=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL); if(!plugin)fail(dlerror());
    Counts counts=(Counts)dlsym(plugin,"nvmtl_vk_counts");if(!counts)fail("no counters");
    io_service_t svc=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));
    if(!svc)fail("no accelerator");
    id<MTLDevice>d=((id(*)(id,SEL,mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc],NSSelectorFromString(@"initWithAcceleratorPort:"),svc);
    IOObjectRelease(svc);if(!d || strcmp(class_getImageName(object_getClass(d)),argv[1]))fail("wrong plugin");
    NSMutableArray *results=[NSMutableArray new];
    for(NSNumber *dimension in @[@64,@1024,@3840]) {@autoreleasepool {
        NSUInteger width=dimension.unsignedIntegerValue,height=width==3840?2160:width;
        IOSurfaceRef s=IOSurfaceCreate((__bridge CFDictionaryRef)@{(id)kIOSurfaceWidth:@(width),
            (id)kIOSurfaceHeight:@(height),(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
        if(!s)fail("surface allocation");
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:width height:height mipmapped:NO];
        td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
        id<MTLTexture>root=[d newTextureWithDescriptor:td iosurface:s plane:0];if(!root)fail("root texture");
        Ivar vram=class_getInstanceVariable(object_getClass(root),"_vramOn");
        if(!vram || !*(BOOL *)((char *)(__bridge void *)root+ivar_getOffset(vram)))fail("not a VRAM import");
        NSMutableArray *samples=[NSMutableArray new];
        uint64_t before,after,bg,im,ig;size_t bytes;counts(&before,&bg,&im,&ig,&bytes);
        for(unsigned i=0;i<232;i++) {@autoreleasepool {
            double start=now();
            id<MTLTexture>t=[d newTextureWithDescriptor:td iosurface:s plane:0];
            double duration=now()-start;
            if(!t || t==root || t.width!=width || t.height!=height || t.usage!=td.usage || t.parentTexture)fail("wrapper contract");
            if(i>=32)[samples addObject:@(duration)];
        }}
        counts(&after,&bg,&im,&ig,&bytes);
        // An alias must remain usable after the caller releases its first wrapper.
        id<MTLTexture>last=[d newTextureWithDescriptor:td iosurface:s plane:0];root=nil;
        id<MTLCommandQueue>q=[d newCommandQueue];id<MTLCommandBuffer>cb=[q commandBuffer];
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=last;
        rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        rp.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
        id<MTLRenderCommandEncoder>e=[cb renderCommandEncoderWithDescriptor:rp];if(!e)fail("render encoder");[e endEncoding];
        id<MTLBuffer>pixel=[d newBufferWithLength:256 options:MTLResourceStorageModeShared];
        id<MTLBlitCommandEncoder>b=[cb blitCommandEncoder];if(!pixel||!b)fail("readback");
        [b copyFromTexture:last sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(1,1,1)
            toBuffer:pixel destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256];[b endEncoding];
        [cb commit];[cb waitUntilCompleted];const uint8_t*p=pixel.contents;
        if(cb.status!=MTLCommandBufferStatusCompleted || p[0] || p[1] || p[2]!=255 || p[3]!=255)fail("alias lifetime pixels");
        [results addObject:@{@"width":@(width),@"height":@(height),@"samples_ms":samples,
            @"import_buffer_allocations":@(after-before),@"wrappers_including_warmup":@232,@"verified":@YES}];
        CFRelease(s);
    }}
    NSData*json=[NSJSONSerialization dataWithJSONObject:@{@"plugin":@(argv[1]),@"results":results} options:0 error:nil];
    fwrite(json.bytes,1,json.length,stdout);fputc('\n',stdout);
}}
