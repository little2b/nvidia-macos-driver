# RTX 4070 SUPER / Ryzen 7 7700 优化记录

日期：2026-10-16。基于上游 `b737dedc11fa4edbcb4abe8d51e94cebfed21ac6`。

## 本机与结论

- Ryzen 7 7700，32 GiB RAM，RTX 4070 SUPER / AD104，12 GiB VRAM。
- macOS 26.7.1 / 25G241，3840×2160、160 Hz，逻辑分辨率 1920×1080。
- BAR1 为 8 GiB；NVAccel、NVRM、NVRMFB、NVRMAGDC 均加载。
- 已通过临时对照验证：移除 `-nvrmnoflip`、使用零拷贝显示后，用户明显感到更流畅，没有闪屏或黑屏。EFI 修改已有独立备份。
- 本轮源码优化减少提交资源的重复扫描，并复用仍有存活包装的 IOSurface 显存导入。
- 已有的同键着色器翻译合并补丁保留；下面的显存导入对照两组都含此补丁，不将它的收益算入本轮数据。
- 新插件仅完成独立进程验证和候选打包；尚未取得本轮候选安装后的桌面帧时间、睡眠唤醒或长期稳定性结果。

## 显示链路与实际热点

```text
应用 / Core Animation / WindowServer
  -> NVMTLDevice、NVMTLObjects：纹理、IOSurface、命令编码
  -> NVMTLEventSync：提交顺序、资源保留、同步、发布结果
  -> nvmtl_vk：Metal 到 Vulkan，上传、描述符、命令缓冲区
  -> Mesa NVK + NVRM：命令提交、内存映射、GPU 完成事件
  -> NVAccel：桌面显示管线，选取已完成的 IOSurface
  -> NVRMFB：NVKMS 翻帧、显示完成确认、垂直同步
```

显示帧缓冲区的当前实现是 `kexts/NVRMFB/fb/nvrm-fb.cpp`。仓库还保留
`kexts/NVRM/fb/nvrm-fb.cpp` 旧副本；不能把旧副本里的固定 60 Hz 定时器当成当前驱动的限制。
当前实现使用硬件 vblank，并按模式时序计算备用定时器周期。
本机 IORegistry 的像素时钟 / 每帧总像素数为 1,398,400,000 / 8,740,000 = 160 Hz。

用户拖动窗口时采集了 10 秒、5 ms 间隔的 WindowServer 栈。采样包含 1615 个主线程观察点；
图像上传函数有 232 个栈顶样本，部分样本落在 streaming-load 复制循环。
同一 IOSurface 的纹理包装还反复进入 `nvmtl_vk_surface_vram`、`nvk_AllocateMemory` 和内核导入调用。
这些是采样热点，不能把跨线程、父子调用的样本相加当成 CPU 百分比。
该采样的插件 UUID 对应缓存优化前的备份，后续磁盘上的插件已更新；不能将它冒充新候选的采样。

同期 20 秒只读计数中，499 次呈现全部走零拷贝，复制等待、显示失败、异步丢弃、显存申请拒绝没有新增。
翻帧调用平均约 4.22 ms，包含等待显示器接收画面，并非纯 GPU 绘制时间，也不代表显示器只有约 25 FPS。

## 补丁一：提交资源一次分类

`plugin/NVMTLEventSync.m` 在提交前一次生成输入、输出缓冲区集合，完成时直接使用输出集合。
原来提交前与完成后各扫描一次完整资源集。保留去重、只读纹理保护、managed 回写、失败隔离和事件顺序；
缓冲区实际字节仍在提交前上传、GPU 完成后回写。

Ryzen 7 7700 上，11 轮交替 CPU 微基准的中位数：

| 资源集合 | 原分类时间 | 新分类时间 |
| --- | ---: | ---: |
| 32 个 GPU 资源 | 1.03 µs | 0.46 µs |
| 256 个 GPU 资源 | 7.97 µs | 3.27 µs |
| 288 个混合资源 / 别名 | 11.70 µs | 6.13 µs |
| 1152 个混合资源 / 别名 | 44.63 µs | 22.33 µs |

五轮交替 4K 离屏清屏与像素校验，32 资源时提交中位数均约 0.205 ms，256 资源时约
0.233 -> 0.231 ms。尾延迟有明显波动，32 资源组的候选 P95 更高。因此不能宣称此补丁提升桌面 FPS，
它只是已验证的局部 CPU 开销减少。

## 补丁二：复用存活的 IOSurface 显存导入

`plugin/NVMTLDevice.m` 的弱缓存原来只缓存无法直接导入显存的 private image。
现在成功导入显存的根纹理也进入缓存，同一 surface / plane / 尺寸 / 格式 / 资源选项的后续包装使用纹理视图。
视图保留根纹理；最后一个包装释放后，弱缓存不额外保留显存。

若首个包装没有 storage-image 能力，而后续请求 ShaderWrite，则重新创建具备该能力的导入，
不能把只读导入直接用于计算写入。纹理使用标志与 swizzle 仍由各包装自己的描述符确定。

五轮交替测试，同一 IOSurface 的原包装仍存活，预热 32 次、测量 200 次：

| 尺寸 | 原包装创建中位数 | 新包装创建中位数 | 232 次包装的新导入分配 |
| --- | ---: | ---: | ---: |
| 64×64 | 55 µs | 约 1 µs | 232 -> 0 |
| 1024×1024 | 197.5 µs | 约 1 µs | 232 -> 0 |
| 3840×2160 | 1264.5 µs | 约 1 µs | 232 -> 0 |

这是缓存命中场景；每次出现全新 surface、原包装已全部释放，或描述符不兼容时，仍须导入。
不是“所有渲染快上千倍”，也尚未证明真实桌面的总体收益。

## 验证与未采用的方向

- 完整插件构建、ad-hoc 签名校验通过；构建前后检查源文件摘要，避免混用编译中修改的源码。
- GPU：12 个计算纹理包装案例通过，覆盖先只读后 ShaderWrite、对齐与非对齐行、一个/两个命令缓冲区和两条队列。
- GPU：1000 帧 IOSurface 测试通过，覆盖别名、部分更新、CPU 更新、放弃录制、跨队列同步；另验证首个包装释放后的渲染像素。
- GPU：复制缓冲区双向同步、计算结果、跨队列事件以及 shared/private 传输通过，测试期间 WindowServer 未重启。
- CPU：提交分类、事件发布、纹理生命周期、同步重叠、越界保护和已有着色器合并测试通过。
- AVX2 streaming copy 在本机普通 RAM 和 CPU IOSurface 测试中与现有 SSE4.1 基本持平，未纳入产品代码。
- Core Image 会在独立候选测试进程内再加载已安装插件，造成同名 Objective-C 类冲突；该模式明确跳过，不能视为验证通过。
- 未调高 BAR、未取消完成等待、未打开撕裂。8 GiB BAR 小于 12 GiB VRAM，并不意味着所有图像都落入系统内存；
  当前显存窗口使用量远低于 4 GiB 预算，且无申请拒绝证据。
- WindowServer 已默认使用 GPU 完成事件等待；单纯把 `NVRM_EVENT_WAIT=1` 写入配置不是新的桌面优化。

## 复现

在仓库根目录运行，输出目录需不存在。Vulkan-Headers 使用 `v1.4.362`。

```sh
bash tools/build_local_plugin.sh /path/to/Vulkan-Headers/include /new/output
python3 tools/test_submission_copies.py
python3 tools/bench_submission_discovery.py /new/discovery.json
python3 tools/check_local_plugin.py /new/output/NVMTLDriver /new/hardware-results
python3 tools/bench_event_wait.py /new/compute-results --rounds 1 --plugin /new/output/NVMTLDriver
```

计算着色器测试需要匹配的 `libnvmtl_translate.dylib` 位于插件旁。完整运行时文件应来自同一已安装版本。
这些命令不会安装驱动。

```sh
xcrun clang -O1 -fobjc-arc tools/bench_surface_import.m -framework Foundation \
  -framework Metal -framework IOKit -framework IOSurface -o /tmp/bench_surface_import
/tmp/bench_surface_import /absolute/path/to/NVMTLDriver
python3 tools/collect_display_counters.py /new/display-counters.json --seconds 20
```

`bench_surface_import` 输出所有原始样本及导入分配计数。对照需保持相同运行时、编译选项和背景负载，
交替运行两组。使用 `bench_desktop_submit.py` 可另外比较离屏提交；它不调用真实显示呈现。

下一步是安装候选、正常重启后重复相同的拖动和滚动，再采集 WindowServer 栈及显示计数。
同时需要覆盖新开窗口、缩放、不同刷新率和睡眠唤醒，才能把局部测试结论扩展到桌面稳定性。

## 后续：保留优化，处理拖动掉帧

用户明确现象为拖动时掉帧，并要求保留现有优化。当前版本采样 UUID 为
`62120792-18FB-3689-A510-8C4562CA8AA8`。新的 10 秒调用栈仍显示主线程的
`nvmtlSurfaceIn` / `nvmtl_vk_image_write` 和重复的 `nvmtl_vk_surface_vram` 导入。
原有弱缓存只能在包装尚未全部释放时命中，不能跨越两帧之间所有包装都释放的间隙。

本轮只读观测：

- CVDisplayLink 8 秒收到 1280 次回调，名义刷新率 160 Hz，回调间隔中位数 6.25 ms、P95 6.70 ms。
- 持续拖动的 20 秒计数收到 884 次显示更新；半秒窗口约 30～62 次/秒，中位数约 44 次/秒。
  这是送屏活动计数，未逐帧测量屏幕实际扫描结果。
- 全部更新走零拷贝；没有新增复制等待、翻帧失败、异步丢弃、显示完成超时或显存拒绝。
- 因此不能把掉帧归因于“160 Hz 未生效”；进一步重点是合成、上传和每帧资源准备。

新增 `nvmtl_surface_keepalive.h`：每个设备保留最近的显存导入根纹理，以便原包装释放后仍能复用。
WindowServer 默认预算为 128 MiB、64 项，按 IOSurface 分配大小向上取整至 64 KiB 计费；
插入之前按最近使用顺序释放缓存引用，超大 surface 不缓存。应用或在途命令自身持有的内存不计入此缓存预算。
其他进程默认关闭。`NVMTL_SURFACE_CACHE_MB=0` 可禁用这项新增缓存；合法范围为 0～256。
该设置由进程初始化读取，独立测试在插件加载后覆盖，不写系统配置。

仍使用现有 IOSurface seed、同步和 dirty 发布逻辑；没有取消 GPU 完成等待，也没有调整零拷贝、异步送屏或刷新率。
缓存淘汰只释放额外引用，不影响调用者或在途命令仍持有的对象。

同一候选二进制关闭/开启缓存，三轮交替：每帧修改 4K surface 的全部 CPU 像素，创建包装、验证上传像素、
GPU 清屏并验证结果，随后释放所有包装，循环使用三张 surface。16 帧预热、80 帧计时：

| 指标 | 关闭跨帧缓存 | 128 MiB 跨帧缓存 |
| --- | ---: | ---: |
| 包装创建时间中位数 | 1.372 ms | 0.0205 ms |
| 准备/上传/绘制/校验总耗时中位数 | 6.2825 ms | 5.3200 ms |
| 96 帧缓冲区分配数（含测试辅助缓冲区） | 98 | 5 |

总耗时减少约 15.3%，但不包含 CPU 写入测试图案所花的时间，也不执行真实桌面呈现，不能当作桌面 FPS 提升。
三轮候选结果约 4.21～5.34 ms，说明背景负载仍影响测量。
额外测试六张 4K surface 触发淘汰、1 MiB 预算拒绝超大条目，均通过新像素和 GPU 写入校验。

开启缓存后，复制缓冲区、12 个计算纹理包装案例、1000 帧别名/部分更新/CPU 更新/跨队列测试通过。
CPU 缓存测试覆盖字节和项数上限、最近使用淘汰、替换、外部引用、两万次插入淘汰与对象释放。

复现（候选插件路径及新输出目录自行指定）：

```sh
python3 tools/test_surface_keepalive.py
python3 tools/bench_surface_churn.py /absolute/plugin /new/churn-results --rounds 3
python3 tools/check_local_plugin.py /absolute/plugin /new/hardware-results --surface-cache-mb 128
```

本轮候选已打包，尚未获得安装后的真实拖动对照。需要安装并正常重启后再次测量；
若无整体收益，不应继续扩大预算来掩盖其他上传或同步瓶颈。
