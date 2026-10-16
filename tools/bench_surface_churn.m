// Recreate all wrappers between frames; verify fresh CPU pixels and GPU writes.
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
static void readPixel(id<MTLCommandQueue>q,id<MTLTexture>t,id<MTLBuffer>out,unsigned x,unsigned y,const unsigned char *want) {
    id<MTLCommandBuffer>c=[q commandBuffer];id<MTLBlitCommandEncoder>b=[c blitCommandEncoder];
    if(!c||!b)fail("readback encoder");
    [b copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(x,y,0) sourceSize:MTLSizeMake(1,1,1)
        toBuffer:out destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256];
    [b endEncoding];[c commit];[c waitUntilCompleted];
    if(c.status!=MTLCommandBufferStatusCompleted || memcmp(out.contents,want,4))fail("stale/mismatched pixels");
}
int main(int argc,char**argv) {@autoreleasepool {
    if(argc!=4 && argc!=5)fail("usage: probe plugin cache-MiB surface-count [width]");
    int mb=atoi(argv[2]),ns=atoi(argv[3]);if(mb<0||mb>256||ns<1||ns>8)fail("invalid limits");
    void*plugin=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);if(!plugin)fail(dlerror());
    setenv("NVMTL_SURFACE_CACHE_MB",argv[2],1); // after release constructor, before device
    Counts counts=(Counts)dlsym(plugin,"nvmtl_vk_counts");if(!counts)fail("counters unavailable");
    io_service_t svc=IOServiceGetMatchingService(kIOMainPortDefault,IOServiceMatching("NVAccel"));if(!svc)fail("no accelerator");
    id<MTLDevice>d=((id(*)(id,SEL,mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc],NSSelectorFromString(@"initWithAcceleratorPort:"),svc);
    IOObjectRelease(svc);if(!d||strcmp(class_getImageName(object_getClass(d)),argv[1]))fail("wrong plugin");
    const unsigned w=argc==5 ? (unsigned)atoi(argv[4]) : 3840;
    if(w<1024 || w>4096)fail("width must be 1024..4096");
    const unsigned h=w>=3000?2160:1080;
    IOSurfaceRef surfaces[8]={0};
    for(int i=0;i<ns;i++) {
        surfaces[i]=IOSurfaceCreate((__bridge CFDictionaryRef)@{(id)kIOSurfaceWidth:@(w),(id)kIOSurfaceHeight:@(h),
            (id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});if(!surfaces[i])fail("surface allocation");
    }
    MTLTextureDescriptor*td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
    td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
    id<MTLCommandQueue>q=[d newCommandQueue];id<MTLBuffer>pixel=[d newBufferWithLength:256 options:MTLResourceStorageModeShared];if(!q||!pixel)fail("queue/buffer");
    NSMutableArray*create=[NSMutableArray new],*frames=[NSMutableArray new];
    uint64_t before,after,bg,im,ig;size_t bytes;counts(&before,&bg,&im,&ig,&bytes);
    for(unsigned f=0;f<96;f++) {@autoreleasepool {
        IOSurfaceRef s=surfaces[f%ns];unsigned char value=(unsigned char)(17+f);
        if(IOSurfaceLock(s,0,NULL))fail("CPU lock");
        unsigned char*base=IOSurfaceGetBaseAddress(s);size_t row=IOSurfaceGetBytesPerRow(s);
        // Update every pixel after every prior wrapper has been released.
        for(unsigned y=0;y<h;y++)memset(base+y*row,value,w*4);
        if(IOSurfaceUnlock(s,0,NULL))fail("CPU unlock");
        double begin=now();
        id<MTLTexture>t=[d newTextureWithDescriptor:td iosurface:s plane:0];
        double created=now();if(!t)fail("texture allocation");
        unsigned char expected[4]={value,value,value,value};
        readPixel(q,t,pixel,(f*31)%w,(f*17)%h,expected);
        readPixel(q,t,pixel,w-1,h-1,expected);
        id<MTLCommandBuffer>c=[q commandBuffer];MTLRenderPassDescriptor*rp=[MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture=t;rp.colorAttachments[0].loadAction=MTLLoadActionClear;
        rp.colorAttachments[0].storeAction=MTLStoreActionStore;rp.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
        id<MTLRenderCommandEncoder>e=[c renderCommandEncoderWithDescriptor:rp];if(!c||!e)fail("render encoder");[e endEncoding];
        [c commit];[c waitUntilCompleted];if(c.status!=MTLCommandBufferStatusCompleted)fail("GPU write");
        unsigned char red[4]={0,0,255,255};readPixel(q,t,pixel,f%w,f%h,red);
        if(f>=16){[create addObject:@(created-begin)];[frames addObject:@(now()-begin)];}
    }}
    counts(&after,&bg,&im,&ig,&bytes);
    NSDictionary*r=@{@"plugin":@(argv[1]),@"cache_MiB":@(mb),@"surfaces":@(ns),@"width":@(w),@"height":@(h),@"verified_frames":@96,
        @"create_ms":create,@"frame_ms":frames,@"buffer_allocations":@(after-before),@"verified":@YES};
    NSData*j=[NSJSONSerialization dataWithJSONObject:r options:0 error:nil];fwrite(j.bytes,1,j.length,stdout);fputc('\n',stdout);
    for(int i=0;i<ns;i++)CFRelease(surfaces[i]);
}}
