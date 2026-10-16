#ifndef NVMTL_PROBE_CONFIG_H
#define NVMTL_PROBE_CONFIG_H
#include <stdio.h>
#include <stdlib.h>
// Call after dlopen: the release plugin clears inherited NVMTL_* variables.
static void nvmtl_probe_config(void) {
    const char *value=getenv("NM_TEST_SURFACE_CACHE_MB");
    if (!value) return;
    char *end=NULL;long mb=strtol(value,&end,10);
    if (!*value || !end || *end || mb<0 || mb>256) {
        fprintf(stderr,"Invalid NM_TEST_SURFACE_CACHE_MB\n");exit(2);
    }
    setenv("NVMTL_SURFACE_CACHE_MB",value,1);
}
#endif
