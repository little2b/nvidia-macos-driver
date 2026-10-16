#!/usr/bin/env python3
"""Compare production padded-row copying with the previous per-row fence path.

This measures CPU copies from ordinary RAM and CPU-created IOSurfaces only.
Neither source is asserted to be a write-combining GPU mapping, and these are
not desktop frame times. No installed driver is loaded or replaced.
"""
import argparse
import json
import pathlib
import statistics
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=pathlib.Path, help='New JSON results file')
parser.add_argument('--rounds', type=int, default=9)
args = parser.parse_args()
if args.output.exists():
    parser.error('output already exists')
if args.rounds < 3:
    parser.error('use at least three measured rounds')

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / 'plugin/nvmtl_vk.c').read_text()
start = source.index('#if defined(__x86_64__)\n#include <immintrin.h>')
helpers = source[start:source.index('int nvmtl_vk_image_write(', start)]
preamble = r'''
#import <Foundation/Foundation.h>
#import <IOSurface/IOSurface.h>
#include <assert.h>
#include <time.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
'''
body = r'''
// Original image_write staging loop; the flat helper above is unchanged.
static void baseline(void *dst,const void *src,size_t tight,size_t stride,uint32_t rows) {
    if(stride==tight) nvmtl_copy_from_surface(dst,src,tight*(size_t)rows);
    else for(uint32_t y=0;y<rows;y++)
        nvmtl_copy_from_surface((char*)dst+(size_t)y*tight,(const char*)src+(size_t)y*stride,tight);
}
typedef void (*copyfn)(void*,const void*,size_t,size_t,uint32_t);
static double measure(copyfn fn,void *dst,const void *src,size_t tight,size_t stride,uint32_t rows) {
    uint64_t t=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
    for(int i=0;i<12;i++) fn(dst,src,tight,stride,rows);
    return (clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)-t)/1e6/12.;
}
int main(int argc,char **argv) { @autoreleasepool {
    assert(argc==2);
    int rounds=atoi(argv[1]);
    NSMutableArray *results=[NSMutableArray new];
    const size_t widths[]={1024,1921,3839,3840,3840};
    const uint32_t heights[]={1024,1080,2160,2160,2160};
    const size_t pads[]={64,60,4,0,64};
    for(unsigned k=0;k<sizeof widths/sizeof widths[0];k++) {
        const size_t tight=widths[k]*4,stride=tight+pads[k],length=stride*heights[k];
        const uint32_t rows=heights[k];
        IOSurfaceRef surf=IOSurfaceCreate((__bridge CFDictionaryRef)@{
            (id)kIOSurfaceWidth:@(widths[k]),(id)kIOSurfaceHeight:@(rows),
            (id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfaceBytesPerRow:@(stride),
            (id)kIOSurfaceAllocSize:@(length),(id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
        assert(surf && IOSurfaceGetAllocSize(surf)>=length && IOSurfaceGetBytesPerRow(surf)==stride);
        assert(!IOSurfaceLock(surf,0,NULL));
        void *surface=IOSurfaceGetBaseAddress(surf),*dest=NULL,*ram=NULL;
        assert(surface && !posix_memalign(&dest,64,length) && !posix_memalign(&ram,64,length));
        for(size_t i=0;i<length;i++) ((unsigned char*)ram)[i]=(unsigned char)(i*17+19);
        memcpy(surface,ram,length);memset(dest,0,length);
        for(int kind=0;kind<2;kind++) {
            const void *src=kind?surface:ram;
            NSMutableArray *a=[NSMutableArray new],*b=[NSMutableArray new];
            for(int round=0;round<=rounds;round++) {
                double x,y;
                if(round%2) {
                    y=measure(nvmtl_copy_surface_rows,dest,src,tight,stride,rows);
                    x=measure(baseline,dest,src,tight,stride,rows);
                } else {
                    x=measure(baseline,dest,src,tight,stride,rows);
                    y=measure(nvmtl_copy_surface_rows,dest,src,tight,stride,rows);
                }
                for(uint32_t row=0;row<rows;row++)
                    assert(!memcmp((char*)dest+(size_t)row*tight,(const char*)src+(size_t)row*stride,tight));
                if(round) {[a addObject:@(x)];[b addObject:@(y)];}
            }
            [results addObject:@{@"memory":kind?@"CPU-created IOSurface":@"ordinary RAM",
                @"width":@(widths[k]),@"height":@(rows),@"row_bytes":@(stride),@"tight_bytes":@(tight),
                @"baseline_ms":a,@"candidate_ms":b}];
        }
        IOSurfaceUnlock(surf,0,NULL);CFRelease(surf);free(ram);free(dest);
    }
    NSData *j=[NSJSONSerialization dataWithJSONObject:results options:0 error:nil];
    assert(j);fwrite(j.bytes,1,j.length,stdout);
}}
'''
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory)
    (path / 'bench.m').write_text(preamble + helpers + body)
    subprocess.run(['xcrun', 'clang', '-O2', '-fobjc-arc', str(path / 'bench.m'),
                    '-framework', 'Foundation', '-framework', 'IOSurface',
                    '-o', str(path / 'bench')], check=True)
    result = subprocess.run([str(path / 'bench'), str(args.rounds)], check=True,
                            text=True, capture_output=True)
rows = json.loads(result.stdout)
for row in rows:
    before = statistics.median(row['baseline_ms'])
    after = statistics.median(row['candidate_ms'])
    row['baseline_median_ms'] = before
    row['candidate_median_ms'] = after
    row['reduction_percent'] = (before - after) / before * 100
report = {'scope': 'CPU copy microbenchmark; no GPU or desktop FPS; no verified write-combining mapping',
          'measured_rounds': args.rounds, 'iterations_per_round': 12, 'results': rows}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2) + '\n')
for row in rows:
    print(f"{row['memory']}: {row['width']}x{row['height']} stride={row['row_bytes']}: "
          f"{row['baseline_median_ms']:.4f} -> {row['candidate_median_ms']:.4f} ms "
          f"({row['reduction_percent']:+.1f}% reduction)")
