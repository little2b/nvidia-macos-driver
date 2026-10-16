#!/usr/bin/env python3
"""A presentation callback must not wait for a ready mark emitted after itself."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
def finish(source):
    start=source.index('- (void)nvmtlFinishExecution:(NSError *)error {')
    return source[start:source.index('- (void)nvmtlFinishCallbacks', start)]
fixture=r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#include <time.h>
typedef struct { int unused; } Commands;
@interface NVMTLSubmissionPart:NSObject {@public void *inflight; Commands commands;} @end
@implementation NVMTLSubmissionPart @end
static BOOL gpuDone,published; static unsigned presents,readyMarks; static BOOL timedOut;
static dispatch_semaphore_t ready;
static NSMutableArray *nvmtl_quarantined_parts;
static int nvmtl_gsub_finish(NVMTLSubmissionPart *p) {p->inflight=NULL;gpuDone=YES;return 0;}
static int nvmtl_vk_interval_seconds(void *t,double *a,double *b) {*a=1;*b=2;return 1;}
static void nvmtl_surface_finish_writes(id cb) {
    assert(gpuDone && published);readyMarks++;dispatch_semaphore_signal(ready);
}
static void nvmtl_res2_cb_done(id cb) {}
#define NVMTLResSet NSMutableSet
@interface NVMTLCommandBuffer:NSObject {@public
 BOOL _scheduleAfterPublish,_gpuTimingInvalid,_gpuTimingSeen,_executionDone;
 NSMutableArray *_parts,*_presentations,*_scratch;NSUInteger _nextPart,_e78RingOff;
 id _present,_e78Ring;void *_g79BRing[2];size_t _g79BLen[2];
 NSMutableSet *_resources,*_stTwins,*_surfOverwrites;
 int _gpuTiming;double _gpuStart,_gpuEnd,_legacyKernelEnd;
 NSError *_cbError;MTLCommandBufferStatus _cbstatus;NSCondition *_completionCondition;
}
- (void)nvmtlFinishExecution:(NSError *)error;
@end
@implementation NVMTLCommandBuffer
- (void)nvmtlMarkScheduled {}
- (void)nvmtlCollectDeviceTiming:(Commands *)c {}
- (void)nvmtlReleaseCapacity {}
'''
checks=r'''
@end
int main(void) {@autoreleasepool {
 for(unsigned failure=0;failure<2;failure++) {
    gpuDone=NO;published=YES;presents=readyMarks=0;timedOut=NO;
    ready=dispatch_semaphore_create(0);
    NVMTLCommandBuffer *cb=[NVMTLCommandBuffer new];
    NVMTLSubmissionPart *part=[NVMTLSubmissionPart new];part->inflight=(void *)1;
    cb->_parts=[NSMutableArray arrayWithObject:part];
    cb->_completionCondition=[NSCondition new];
    cb->_presentations=[NSMutableArray arrayWithObject:[^{
        assert(gpuDone && published);
        // A display callback waits on the kernel's pending mark before scanning out.
        timedOut=dispatch_semaphore_wait(ready,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_MSEC))!=0;
        presents++;
    } copy]];
    [cb nvmtlFinishExecution:failure?[NSError errorWithDomain:@"test" code:1 userInfo:nil]:nil];
    if(timedOut){fprintf(stderr,"FAIL: presentation waited for its own late ready mark\n");return 1;}
    assert(readyMarks==1 && presents==(failure?0:1));
    assert(cb->_executionDone && cb->_parts.count==0 && cb->_presentations.count==0);
    assert(cb->_cbstatus==(failure?MTLCommandBufferStatusError:MTLCommandBufferStatusCompleted));
 }
 puts("PASS: ready after GPU completion, before presentation; errors suppress presentation");
}}
'''
current=(root/'plugin/NVMTLEventSync.m').read_text()
upstream=subprocess.check_output(['git','show','v2.1.0:plugin/NVMTLEventSync.m'],cwd=root,text=True)
with tempfile.TemporaryDirectory(prefix='nvmtl-present-order-') as temp:
    p=Path(temp)
    for name,source in [('current',current),('upstream',upstream)]:
        (p/(name+'.m')).write_text(fixture+finish(source)+checks)
        subprocess.run(['xcrun','clang','-O1','-fobjc-arc',str(p/(name+'.m')),
                        '-framework','Foundation','-framework','Metal','-o',str(p/name)],check=True)
        result=subprocess.run([str(p/name)],capture_output=True,text=True,timeout=5)
        if name=='current':
            assert result.returncode==0,result.stderr
            print(result.stdout.strip())
        else:
            assert result.returncode==1 and 'late ready mark' in result.stderr
            print('PASS: unmodified 2.1.0 reproduces the presentation timeout')
