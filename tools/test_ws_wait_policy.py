#!/usr/bin/env python3
"""Exercise production WindowServer wait selection, including boot/mode transitions.

No driver is loaded: syscall and kernel mark results are injected. The old
policy is a negative control for the copy-path startup and commit-boundary cases.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def helpers(source):
    start = source.index('@interface NVMTLPendingSurfaceWrite')
    return source[start:source.index('@implementation NVMTLSubmissionPart', start)]


def wait_method(source):
    start = source.index('- (BOOL)commitAndWaitUntilSubmitted {')
    return source[start:source.index('- (void)commit {', start)]


FIXTURE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <assert.h>
#include <stdatomic.h>
#include <string.h>
#include <time.h>
#include <sys/sysctl.h>
static int flip=1, asyncMode=1, kernel=1, failFlip, failAsync, badSize, rejectAll;
static unsigned rejectedID, probes, pendingCalls, readyCalls, flagReads;
static uint64_t now=1000000000ull;
static NSMutableSet *kernelPending;
static const char *scenario, *processName="WindowServer";
static int test_sysctl(const char *name,void *out,size_t *size,void *newp,size_t newlen) {
    assert(!newp && !newlen);flagReads++;
    int v;
    if (!strcmp(name,"debug.nvaccel_iop_flip")) { if(failFlip)return -1;v=flip; }
    else { assert(!strcmp(name,"debug.nvaccel_iop_async"));if(failAsync)return -1;v=asyncMode; }
    assert(*size==sizeof(int));memcpy(out,&v,sizeof v);if(badSize)*size=1;return 0;
}
static uint64_t test_clock(clockid_t which) {return now;}
static const char *test_progname(void) {return processName;}
#define sysctlbyname test_sysctl
#define clock_gettime_nsec_np test_clock
#define getprogname test_progname
static uint32_t nvmtl_vk_surface_gpu_mark(uint32_t sid,int pending) {
    if(!sid){probes++;return kernel?0:1;}
    if(pending)pendingCalls++;else readyCalls++;
    if(!kernel || (pending && (rejectAll || sid==rejectedID)))return 1;
    if(pending)[kernelPending addObject:@(sid)];else[kernelPending removeObject:@(sid)];
    return 0;
}
static void nvlog(const char *format,...) {}
struct __IOSurface {unsigned id,seed;};
#define IOSurfaceGetID test_surface_id
#define IOSurfaceGetSeed test_surface_seed
static unsigned IOSurfaceGetID(IOSurfaceRef s) {return s->id;}
static unsigned IOSurfaceGetSeed(IOSurfaceRef s) {return s->seed;}
@interface NVMTLTexture:NSObject {@public IOSurfaceRef _surf;BOOL _vramOn;NSUInteger _plane;} @end
@implementation NVMTLTexture @end
@interface NVMTLSubmissionPart:NSObject {@public NSSet *surfaces;} @end
@implementation NVMTLSubmissionPart @end
@interface NVMTLCommandBuffer:NSObject {@public id _queue;NSArray *_parts;unsigned completed,scheduled;}
- (BOOL)commitAndWaitUntilSubmitted;
- (void)commit;
- (void)waitUntilCompleted;
- (void)waitUntilScheduled;
- (MTLCommandBufferStatus)status;
@end
'''

METHODS = r'''
@implementation NVMTLCommandBuffer
- (void)commit {
    nvmtl_ws_mark_pending(self);
    if(!strcmp(scenario,"enable-between-commit-and-wait")) {flip=1;now+=2000000000ull;}
    if(!strcmp(scenario,"disable-flip-between-commit-and-wait")) flip=0;
    if(!strcmp(scenario,"disable-async-between-commit-and-wait")) asyncMode=0;
    if(!strcmp(scenario,"disable-kernel-between-commit-and-wait")) {kernel=0;now+=2000000000ull;}
}
- (void)waitUntilCompleted {completed++;nvmtl_ws_mark_ready(self);}
- (void)waitUntilScheduled {
    scheduled++;
    if(!strcmp(scenario,"disable-flip-while-scheduled") || !strcmp(scenario,"non-windowserver-mode-changes")) flip=0;
    if(!strcmp(scenario,"disable-async-while-scheduled")) asyncMode=0;
    if(!strcmp(scenario,"complete-while-scheduled")) nvmtl_ws_mark_ready(self);
}
- (MTLCommandBufferStatus)status {return MTLCommandBufferStatusCompleted;}
'''

CHECKS = r'''
@end
int main(int argc,char **argv) {@autoreleasepool {
    assert(argc==2);scenario=argv[1];kernelPending=[NSMutableSet new];
    unsigned expectFast=0,expectPending=0,expectReady=0,expectAfterScheduleWait=0;
    if(!strcmp(scenario,"flip-off")) flip=0;
    else if(!strcmp(scenario,"async-off")) asyncMode=0;
    else if(!strcmp(scenario,"both-off")) flip=asyncMode=0;
    else if(!strcmp(scenario,"normal")) {expectFast=1;expectPending=expectReady=2;}
    else if(!strcmp(scenario,"flip-read-failed")) failFlip=1;
    else if(!strcmp(scenario,"async-read-failed")) failAsync=1;
    else if(!strcmp(scenario,"invalid-read-size")) badSize=1;
    else if(!strcmp(scenario,"kernel-off")) kernel=0;
    else if(!strcmp(scenario,"all-marks-refused")) {rejectAll=1;expectPending=2;}
    else if(!strcmp(scenario,"partial-marks-refused")) {rejectedID=20;expectPending=2;expectReady=1;}
    else if(!strcmp(scenario,"empty-surfaces")) {}
    else if(!strcmp(scenario,"enable-between-commit-and-wait")) flip=0;
    else if(!strcmp(scenario,"disable-flip-between-commit-and-wait") ||
            !strcmp(scenario,"disable-async-between-commit-and-wait") ||
            !strcmp(scenario,"disable-kernel-between-commit-and-wait")) expectPending=expectReady=2;
    else if(!strcmp(scenario,"cached-probe-kernel-disabled")) {
        assert(nvmtl_ws_kernel_waits());kernel=0;expectPending=2;
    }
    else if(!strcmp(scenario,"non-windowserver") || !strcmp(scenario,"non-windowserver-mode-changes")) {
        processName="test-application";expectFast=1;
    }
    else if(!strcmp(scenario,"disable-flip-while-scheduled") || !strcmp(scenario,"disable-async-while-scheduled") ||
            !strcmp(scenario,"complete-while-scheduled")) {
        expectFast=expectAfterScheduleWait=1;expectPending=expectReady=2;
    }
    else if(!strcmp(scenario,"explicit-completion-wait")) setenv("NVMTL_WS_WAIT_COMPLETION","1",1);
    else if(!strcmp(scenario,"probe-cache-and-mode-transition")) {
        assert(nvmtl_ws_kernel_waits());assert(probes==1);
        now+=500000000ull;assert(nvmtl_ws_kernel_waits());assert(probes==1);
        flip=0;assert(!nvmtl_ws_kernel_waits());assert(probes==1);
        flip=1;asyncMode=0;assert(!nvmtl_ws_kernel_waits());assert(probes==1);
        asyncMode=1;assert(nvmtl_ws_kernel_waits());assert(probes==1);
        now+=600000000ull;assert(nvmtl_ws_kernel_waits());assert(probes==2);
        kernel=0;now+=1000000001ull;assert(!nvmtl_ws_kernel_waits());assert(probes==3);
        kernel=1;now+=1000000001ull;assert(nvmtl_ws_kernel_waits());assert(probes==4);
        puts("PASS: one-second capability probe, immediate mode fallback, and live recovery");return 0;
    } else {fprintf(stderr,"unknown scenario %s\n",scenario);return 2;}
    struct __IOSurface a={10,1},b={20,1};
    NVMTLTexture *ta=[NVMTLTexture new],*tb=[NVMTLTexture new];ta->_surf=&a;tb->_surf=&b;
    NVMTLSubmissionPart *part=[NVMTLSubmissionPart new];
    part->surfaces=!strcmp(scenario,"empty-surfaces")?[NSSet set]:[NSSet setWithObjects:ta,tb,nil];
    NVMTLCommandBuffer *cb=[NVMTLCommandBuffer new];cb->_parts=@[part,part];
    assert([cb commitAndWaitUntilSubmitted]);
    if(cb->scheduled!=expectFast || cb->completed!=(1-expectFast+expectAfterScheduleWait)) {
        fprintf(stderr,"FAIL %s: completed=%u scheduled=%u expected_scheduled=%u expected_completed=%u\n",
                scenario,cb->completed,cb->scheduled,expectFast,1-expectFast+expectAfterScheduleWait);return 1;
    }
    // Completion is idempotent, and every accepted pending mark is released, including partially refused batches.
    nvmtl_ws_mark_ready(cb);nvmtl_ws_mark_ready(cb);
    assert(pendingCalls==expectPending && readyCalls==expectReady);
    if(kernel)assert(!kernelPending.count);
    printf("PASS: %s\n",scenario);return 0;
}}
'''

SCENARIOS = [
    'flip-off', 'async-off', 'both-off', 'normal', 'flip-read-failed',
    'async-read-failed', 'invalid-read-size', 'kernel-off', 'all-marks-refused',
    'partial-marks-refused', 'empty-surfaces', 'enable-between-commit-and-wait',
    'disable-flip-between-commit-and-wait', 'disable-async-between-commit-and-wait',
    'disable-kernel-between-commit-and-wait', 'cached-probe-kernel-disabled',
    'non-windowserver', 'explicit-completion-wait', 'probe-cache-and-mode-transition',
    'disable-flip-while-scheduled', 'disable-async-while-scheduled', 'complete-while-scheduled',
    'non-windowserver-mode-changes',
]
current = ((ROOT/'plugin/NVMTLEventSync.m').read_text(), (ROOT/'plugin/NVMTLObjects.m').read_text())
previous = tuple(subprocess.check_output(['git','show',f'b2d6e4f:plugin/{name}'],cwd=ROOT,text=True)
                 for name in ('NVMTLEventSync.m','NVMTLObjects.m'))
with tempfile.TemporaryDirectory(prefix='nvmtl-ws-wait-') as directory:
    path=Path(directory)
    mode_only = (current[0], current[1].replace('nvmtl_ws_buffer_kernel_waits(self)', 'nvmtl_ws_kernel_waits()'))
    for name,sources in [('current',current),('before',previous),('mode-only',mode_only)]:
        src=path/f'{name}.m';binary=path/name
        src.write_text(FIXTURE+helpers(sources[0])+METHODS+wait_method(sources[1])+CHECKS)
        subprocess.run(['xcrun','clang','-O1','-fobjc-arc',str(src),'-framework','Foundation',
                        '-framework','Metal','-o',str(binary)],check=True)
        negative = ['flip-off','async-off'] if name=='before' else ['enable-between-commit-and-wait','partial-marks-refused']
        for scenario in SCENARIOS if name=='current' else negative:
            result=subprocess.run([str(binary),scenario],text=True,capture_output=True,timeout=5)
            if name=='current':
                assert result.returncode==0,(scenario,result.stdout,result.stderr)
                print(result.stdout.strip())
            else:
                assert result.returncode==1 and 'FAIL' in result.stderr,(scenario,result.stdout,result.stderr)
                print(f'PASS: {name} negative control reproduces unsafe wait selection for {scenario}')
