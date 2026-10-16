#!/usr/bin/env python3
"""Check production flat and padded surface copies, including protected row tails."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / 'plugin/nvmtl_vk.c').read_text()
start = source.index('#if defined(__x86_64__)\n#include <immintrin.h>')
helpers = source[start:source.index('int nvmtl_vk_image_write(', start)]
fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>
'''
checks = r'''
int main(void) {
    size_t page=(size_t)sysconf(_SC_PAGESIZE), span=page*8;
    unsigned char *mapping=mmap(NULL,span+page,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
    assert(mapping!=MAP_FAILED && !mprotect(mapping+span,page,PROT_NONE));
    unsigned char *output=malloc(span+64); assert(output);
    for(size_t i=0;i<span;i++) mapping[i]=(unsigned char)(i*37+19);
    const size_t lengths[]={0,1,15,16,63,64,65,4095,4096,4097,8191,8192};
    for(unsigned offset=0;offset<32;offset++) for(unsigned dst=0;dst<32;dst++)
        for(unsigned k=0;k<sizeof lengths/sizeof lengths[0];k++) {
            size_t n=lengths[k]; memset(output,0xcd,span+64);
            nvmtl_copy_from_surface(output+32+dst,mapping+offset,n);
            assert(!memcmp(output+32+dst,mapping+offset,n));
            for(size_t i=0;i<32+dst;i++) assert(output[i]==0xcd);
            for(size_t i=32+dst+n;i<span+64;i++) assert(output[i]==0xcd);
        }
    for(size_t tail=0;tail<128;tail++) {
        size_t n=8192+tail; unsigned char *src=mapping+span-n;
        nvmtl_copy_from_surface(output,src,n); assert(!memcmp(output,src,n));
    }
    munmap(mapping,span+page);free(output);

    span=page*64;
    mapping=mmap(NULL,span+page,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
    assert(mapping!=MAP_FAILED && !mprotect(mapping+span,page,PROT_NONE));
    output=malloc(span+64); unsigned char *expected=malloc(span); assert(output && expected);
    for(size_t i=0;i<span;i++) mapping[i]=(unsigned char)(i*37+19);
    const size_t widths[]={0,1,15,63,64,65,4095,4096,4097,8191,8192,15356,15360};
    unsigned cases=0;
    for(unsigned k=0;k<sizeof widths/sizeof widths[0];k++)
        for(size_t padding=0;padding<66;padding+=3)
            for(size_t offset=0;offset<32;offset++) for(size_t dst=0;dst<32;dst+=7) {
                size_t tight=widths[k], stride=tight+padding;
                const uint32_t rows=3;
                memset(output,0xcd,span+64);
                for(uint32_t y=0;y<rows;y++) memcpy(expected+y*tight,mapping+offset+y*stride,tight);
                nvmtl_copy_surface_rows(output+dst,mapping+offset,tight,stride,rows);
                assert(!memcmp(output+dst,expected,tight*rows));
                for(size_t i=0;i<dst;i++) assert(output[i]==0xcd);
                for(size_t i=dst+tight*rows;i<span+64;i++) assert(output[i]==0xcd);
                cases++;
            }
    // The last readable source byte is the last pixel, with no accessible row
    // padding after it. Exercise both aligned streaming and unaligned fallback.
    for(size_t tail=0;tail<128;tail++) {
        size_t tight=8192+tail, stride=(tight+63)&~(size_t)63;
        const uint32_t rows=3;
        unsigned char *src=mapping+span-((rows-1)*stride+tight);
        nvmtl_copy_surface_rows(output,src,tight,stride,rows);
        for(uint32_t y=0;y<rows;y++) assert(!memcmp(output+y*tight,src+y*stride,tight));
        cases++;
    }
    // Guard every source row: the optimized loop must not read the padding
    // between rows, even when it is an entire inaccessible page.
    size_t tight=page*2, stride=page*3;
    unsigned char *rowguards=mmap(NULL,stride*3,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
    assert(rowguards!=MAP_FAILED);
    for(uint32_t y=0;y<3;y++) {
        memset(rowguards+y*stride,(int)y+1,tight);
        assert(!mprotect(rowguards+y*stride+tight,page,PROT_NONE));
    }
    nvmtl_copy_surface_rows(output,rowguards,tight,stride,3);
    for(uint32_t y=0;y<3;y++) for(size_t x=0;x<tight;x++) assert(output[y*tight+x]==y+1);
    munmap(rowguards,stride*3);
    munmap(mapping,span+page);free(output);free(expected);
    printf("PASS: 12288 flat alignment/length cases, 128 flat read boundaries, %u row cases, per-row protected padding\n",cases);
}
'''
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory)
    (path / 'test.c').write_text(fixture + helpers + checks)
    subprocess.run(['clang', '-O2', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
