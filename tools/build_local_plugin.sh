#!/bin/bash
# Build an isolated plugin for probes; never install it into WindowServer.
set -euo pipefail
if [ "$#" != 2 ]; then
  echo "Usage: bash $0 /path/to/Vulkan-Headers/include /new/output/directory" >&2
  exit 2
fi
repo=$(cd "$(dirname "$0")/.." && pwd)
headers=$(cd "$1" && pwd)
[ -f "$headers/vulkan/vulkan_core.h" ] || { echo 'Missing Vulkan headers' >&2; exit 2; }
mkdir "$2"
output=$(cd "$2" && pwd)
shasum -a 256 "$repo"/plugin/* > "$output/source-sha256.txt"
{
  git -C "$repo" rev-parse HEAD
  git -C "$repo" diff HEAD -- plugin
} > "$output/source-state.txt"
xcrun clang -arch x86_64 -bundle -fobjc-arc -O1 -DNVMTL_RELEASE=1 \
  -Wno-protocol -Wno-incomplete-implementation -I"$headers" \
  "$repo/plugin/NVMTLDevice.m" -framework Foundation -framework Metal \
  -framework IOSurface -framework IOKit -o "$output/NVMTLDriver" \
  >"$output/build.log" 2>&1 || { tail -40 "$output/build.log" >&2; exit 1; }
codesign -f -s - "$output/NVMTLDriver"
codesign --verify "$output/NVMTLDriver"
shasum -a 256 "$repo"/plugin/* > "$output/source-after-sha256.txt"
cmp "$output/source-sha256.txt" "$output/source-after-sha256.txt" || {
  echo 'Source changed during build; discard this binary.' >&2; exit 1;
}
xcrun clang --version > "$output/compiler.txt"
xcrun --show-sdk-path >> "$output/compiler.txt"
shasum -a 256 "$headers/vulkan/vulkan_core.h" "$output/NVMTLDriver" > "$output/build-sha256.txt"
echo "Built $output/NVMTLDriver (not installed)."
