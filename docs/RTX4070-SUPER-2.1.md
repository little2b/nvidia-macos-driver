# RTX 4070 SUPER：2.1.0 适配与拖动优化

基线为官方 `v2.1.0`，源码提交 `277a29b`。旧版优化留在
`codex/rtx4070-super-tuning`；本轮在 `codex/rtx4070-super-v2.1` 上工作。
日期：2026-10-16。

## 当前机器与显示状态

Ryzen 7 7700、32 GiB RAM、RTX 4070 SUPER 12 GiB，macOS 26.7.1 / 25G241。
官方更新替换了 Metal 插件、NVK、翻译器及内核组件，本轮复用已安装的 2.1.0 运行时，
只准备替换 Metal 插件。保留 2.1.0 新的 GPU 完成标记与显示等待协议。

实测当前显示模式：1920×1080 逻辑分辨率、3840×2160 像素、约 160 Hz，即 2× HiDPI。
系统还列出 800×450、960×540、1024×576、1280×720 对应的 2× 档位。
设置界面没有“更清晰”等按钮，不意味着 HiDPI 没有启用。
2.1.0 的 `nvfbhidpi` 用于开放更大的缩放桌面；为避免改变渲染负荷，本轮不调整它。
零拷贝持续开启，`-nvrmnoflip` 保持移除。

## 新发现及修复

旧的翻帧超时计数为零，但 2.1.0 新增的 `debug.nvaccel_surface_gpu_timeouts`
在本次启动后累计达到 56，最长等待约 100.93 ms。这是等待合成 GPU 完成的另一种超时。
随后 20 秒观察中该计数保持 56，因此不能认定它解释了当前每一次掉帧。

`NVMTLEventSync.m` 原来的完成顺序是：GPU 完成、调用 presentation action、发送 surface-ready 标记。
如果 presentation action 进入显示管线并等待该标记，执行回调的线程就无法继续发送标记，
只能等超时。现在先完成 surface-write 跟踪与 ready 标记，再调用 presentation action。
GPU 完成等待仍在前面，错误路径仍不呈现图像。

`tools/test_present_ready_order.py` 编译生产方法，模拟等待 ready 标记的呈现回调。
未修改的 2.1.0 触发自等待超时；修复后通过。这个测试验证了调用顺序缺陷，
并未证明本机那 56 次超时全部来自此路径。真实 WindowServer 的改善必须在安装后验证。

## 保留并适配的优化

- 同一 IOSurface 的存活包装共享显存导入，兼容性不满足时重新创建。
- WindowServer 的跨帧导入缓存默认限制为 128 MiB / 64 项，其他进程默认关闭。
  CPU 修改后仍通过 IOSurface seed 检查重新上传，淘汰不会释放其他引用仍在使用的资源。
- 提交资源只分类一次，实际上传和回写仍遵守 GPU 同步顺序。
- 同缓存键的并发着色器翻译合并，不同键仍可并行，失败可重试。

三轮交替，对同一个 2.1.0 候选显式关闭/开启跨帧缓存；三张 4K surface 每帧修改 CPU 像素，
重建包装、上传、验证像素、GPU 清屏并再次验证，最后释放所有包装：

| 指标 | 缓存关闭 | 缓存 128 MiB |
| --- | ---: | ---: |
| 包装创建时间中位数 | 1.336 ms | 0.022 ms |
| 准备/上传/绘制/校验耗时中位数 | 7.526 ms | 6.2875 ms |
| 96 帧缓冲区分配数，含辅助缓冲区 | 98 | 5 |

整段耗时减少约 16.5%。这不包含生成 CPU 测试图案的时间，不执行真实桌面呈现，也不是“FPS 提升 16.5%”。
另验证了六张 4K surface 触发淘汰以及 1 MiB 预算跳过超大条目；均未读到旧像素。

## 验证

- 完整插件构建和签名通过，构建前后源文件摘要一致。
- GPU：复制缓冲区双向同步、12 个计算纹理包装案例、1000 帧 IOSurface 顺序/别名/部分更新/CPU 更新/跨队列测试通过。
- GPU：计算结果、跨队列事件以及 shared/private 传输校验通过；测试期间 WindowServer 未重启。
- CPU：完成标记顺序（含原版负向对照）、资源分类、surface 队列与并发发布、缓存容量/LRU/生命周期、着色器请求合并通过。
- Core Image 独立候选测试仍跳过，避免同时加载系统插件造成同名类冲突。
- 尚未安装本轮候选，未取得真实桌面帧时间、睡眠唤醒或长期稳定性结论。

复现命令，从仓库根目录执行，输出目录须为新目录：

```sh
bash tools/build_local_plugin.sh /path/to/Vulkan-Headers/include /new/plugin
python3 tools/test_present_ready_order.py
python3 tools/test_surface_keepalive.py
python3 tools/check_local_plugin.py /new/plugin/NVMTLDriver /new/gpu-results --surface-cache-mb 128
python3 tools/bench_surface_churn.py /new/plugin/NVMTLDriver /new/churn-results --rounds 3
python3 tools/collect_display_counters.py /new/display-counters.json --seconds 20
```

计算测试需要将匹配的 `libnvmtl_translate.dylib` 放在候选插件旁。安装包必须使用同一版本的运行时支持文件。
完成标记测试模拟内核等待；普通测试进程不会被内核当成 WindowServer，不能代替实际桌面测试。

安装后应对比相同窗口的拖动，并分别查看 `nvaccel_surface_gpu_timeouts` 与 `nvrmfb_flip_latch_timeouts` 的增量。
如果缓存和顺序修复未带来足够改善，下一步是当前版本的合成/上传调用栈，而不是继续盲目扩大缓存或取消同步。
