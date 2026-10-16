# 2.1.0：启动显示等待与带填充行的上传

本轮基于 `b2d6e4f`，继续保留零拷贝、显存导入复用、128 MiB 跨帧缓存、
提交资源一次分类、着色器请求合并及 ready-before-present 修复。
验证机器：Ryzen 7 7700、RTX 4070 SUPER、macOS 26.7.1 / 25G241，4K 160 Hz。

## 启动阶段等待策略

安装上一版后，用户报告启动稍卡、随后明显好转。累计 GPU-ready 等待超时为 132，
后续多次观察没有增加；复制跳过计数也保持在 114。不能因此认定所有超时都来自同一原因。

源码中的时间顺序是：

1. NVRM 释放启动显示后，继续使用复制显示路径。
2. `autoGo` 明确等待 60 秒，满足 BAR1 和显卡条件才开启 `nvaccel_iop_flip`。
3. 2.1.0 的 GPU-ready 协议却从启动早期就可用。旧插件仅检测该协议，可能在复制阶段就只等待 Scheduled。

本机 IOAcceleratorFamily2 反汇编确认以下锁依赖可同时存在：

- `transactionEnd` 持 accelerator family lock，随后进入显示 command gate。
- `performTransaction` 从 `event_interrupt_gated` 调用，在显示 gate 内运行。
- 复制路径在 `performTransaction` 内等待 ready。
- 插件完成线程先发布 IOSurface dirty 状态，需要 family lock，然后才能发送 ready。

因此存在“显示 gate 等 ready → 发布线程等 family lock → 新事务持 family lock 等显示 gate”的循环。
100 ms 超时可以打破它。这里证明了调用和锁顺序，尚未通过启动时的动态栈证明 132 次超时都属于此循环。

本轮用户态驱动修复：

- 只有实时读取到零拷贝和异步翻帧都已开启，才考虑由内核等待完成；读取失败保守等待 Completed。
- 内核能力探测仍最多每秒一次。显示模式检查不缓存成功结果，避免关闭后继续沿用旧状态。
- 每个命令缓冲区记录实际被内核接受的标记；部分失败时清理已接受标记，但本 CB 自己等待完成。
- 没有标记的 CB 不会因为提交后模式变了而突然跳过完成等待。
- Scheduled 返回后再次复核可见的模式变化；普通应用仍保持 Scheduled 契约。

GPU 完成等待、像素发布和已有 ready-before-present 顺序均保留。零拷贝异步模式就绪后仍走快速路径。
这不是原子的显示路由协议：检查之后发生的 fallback、内核旧有布尔标记重叠等问题仍可能需要单独修复。
本轮没有修改内核扩展或改变超时时长。

`tools/test_ws_wait_policy.py`：23 个正向场景与 4 个负控通过，覆盖开机复制模式、同步模式、
读取失败、部分标记拒绝、提交边界变化、Scheduled 期间变化及正常完成清除标记。
原版和仅加全局模式判断的负控分别复现不安全的等待选择。

## 上传复制优化

原 `nvmtl_vk_image_write` 已对连续行使用一次整幅复制；带行填充时则每行调用 streaming-copy，
重复执行行首、行尾内存栅栏。现在对满足对齐条件的大图像，把栅栏移至整个行循环的两端。

条件和语义：

- 每行有效字节至少 4096，源地址与源步长均为 16 字节对齐，CPU 支持 SSE4.1。
- 每行只复制有效像素，不读 padding；目标允许非对齐地址。
- 源 IOSurface 保持锁定，staging 只在全部复制完成后提交给 GPU。
- 连续行仍使用原路径；短行、非对齐和不支持的 CPU 使用原 fallback。
- 不改变 seed、GPU 等待、资源生命周期或上传内容。

生产代码抽取测试覆盖 12,288 个平面复制案例、128 个读边界、45,888 个二维案例及逐行不可读 padding。
普通 RAM 和 CPU 创建的 IOSurface 微基准显示部分带填充场景的 CPU 复制耗时减少约 16%～48%。
没有单独确认真实 WC GPU 映射的性能，因此不能将这组数据描述为所有 GPU 映射的收益。

完整插件在真实 GPU 上做三轮交替对照。两组均保留 128 MiB 缓存，每帧更新 CPU 像素、重建包装、
上传、读取校验点（含右下角）、GPU 清屏并验证，96 帧中后 80 帧计时：

| 图像大小 | 上一版整段中位数 | 本轮整段中位数 |
| --- | ---: | ---: |
| 1921×1080 | 1.0325 ms | 1.0075 ms |
| 3839×2160 | 4.6015 ms | 3.9845 ms |

接近 4K 的场景约减少 13.4%，但三个单轮中位数有交叠；这是离屏上传/绘制/校验，不是桌面 FPS。
WindowServer 的启动等待修复不能由普通测试进程完整验证，需要下一次正常启动观察。

## 验证与部署

- 完整 release 插件构建与签名检查通过，源码摘要在构建前后相同。
- GPU 复制缓冲区、12 个计算包装案例、1000 帧 IOSurface 别名/部分更新/CPU 更新/跨队列测试通过。
- 新尺寸上传的像素与右下角校验通过，测试期间 WindowServer 未重启。
- 完成标记顺序、surface 并发发布、提交回写和新的等待策略回归通过。
- 复制测试的尾部和每行 padding 均使用保护页检查越界。

本轮只替换 Metal 插件。安装前完整备份当前已优化的 bundle；回退恢复上一候选，不撤销前面的优化。
安装器验证候选、备份、当前 bundle 及 NVK/内核组件摘要，通过同盘原子交换替换，保留旧目录。
当前运行的 WindowServer 不会因替换文件就自动加载新插件，需保存工作后正常重启。

复现：

```sh
python3 tools/test_ws_wait_policy.py
python3 tools/test_surface_stream_copy.py
python3 tools/bench_surface_rows.py /new/rows.json
python3 tools/check_local_plugin.py /absolute/plugin /new/hardware --surface-cache-mb 128
python3 tools/bench_surface_churn.py /absolute/plugin /new/churn --width 3839
```

启动复测需同时观察 `nvaccel_iop_flip` 的阶段、`nvaccel_surface_gpu_timeouts` 增量、
`nvaccel_iop_copy_skip` 和持续拖动表现。暂不把未复现的内核协议边界或描述符位图问题混入本轮性能补丁。
