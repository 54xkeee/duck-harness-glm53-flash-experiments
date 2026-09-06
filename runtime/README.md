# 加固运行入口

此覆盖层把第二轮冻结实现、实验后反馈修复和新的 Linux 隔离执行器组合为一个**新目录**。冻结资料保持原字节，不在原实验环境上直接打补丁。新运行版尚未重跑模型/游戏性能实验；旧成绩不属于本覆盖层。

## 支持范围

- Ubuntu 24.04 x86-64，包括具备所需 namespace 功能的 WSL2。
- 系统 `/usr/bin/python3` 指向 Python 3.12；安装发行版安全更新。
- Bubblewrap 0.9 或更新版本、libseccomp.so.2、prlimit；非特权 user、mount、PID、IPC、network namespaces 可用。
- Windows 原生、其他架构、缺失工具或 namespace 被宿主策略禁用时，**停止执行且没有普通子进程回退**。
- 主机、运行文件、依赖与宿主动作回调属于可信边界；具体威胁模型见 [SECURITY.md](../SECURITY.md)。

安装系统组件后，从仓库根目录执行下面的流程。命令中的工作目录由操作者选择，必须在归档仓库之外且尚不存在；脚本不会覆盖既有部署。

```bash
sudo apt-get update
sudo apt-get install bubblewrap libseccomp2 python3-venv
/usr/bin/python3 runtime/check_environment.py
/usr/bin/python3 runtime/build_runtime.py --verify-frozen
/usr/bin/python3 runtime/build_runtime.py /tmp/duck-live

/usr/bin/python3 -m venv /tmp/duck-host-env
/tmp/duck-host-env/bin/python -m pip install --upgrade pip==26.2.1
/tmp/duck-host-env/bin/python -m pip install --require-hashes -r runtime/requirements-test.txt
/tmp/duck-host-env/bin/python runtime/tests/test_isolation.py
export PYTHONPATH=/tmp/duck-live/ARC3-Inference:/tmp/duck-live/tufa-arc-agi-framework/src
/tmp/duck-host-env/bin/python -m pytest -q -p no:cacheprovider /tmp/duck-live/ARC3-Inference/tests/test_prediction_io.py
/tmp/duck-host-env/bin/python -m inference.framework.run --help
```

这组命令仅构建、验证和显示帮助，不发送模型请求。正式运行另需操作者的游戏与模型配置；当前资料不是完整 GPU 服务发行包。模型连接只在可信宿主侧，密钥不传给 Python 工具沙箱。

宿主包入口会加载图像/工具代理模块，所以先安装宿主测试依赖，再运行隔离测试；子进程内部仍只使用固定的系统 Python 标准库，不挂载宿主虚拟环境。

`requirements-test.txt` 固定宿主离线集成测试的依赖及下载哈希。它不是冻结实验当时的原始依赖清单，也不包含 GPU 推理服务。更新时从 `requirements-test.in` 重新生成、扫描并回归测试。

默认寻找系统 `bwrap`。若使用单独解包的发行版程序，可在可信宿主设置绝对路径 `DUCK_BWRAP`；此路径是管理员配置，不得来自模型输出或不可信项目内容。

## 固定执行边界

| 项目 | 当前策略 |
|---|---|
| 文件系统 | 只读系统 Python、标准库及 x86-64 共享库目录；用户目录、仓库、宿主临时文件不挂载 |
| 文件写入 | 根目录、临时目录和运行文件均只读；不提供可写文件空间 |
| 网络 | 独立 network namespace；seccomp 同时阻断网络和 Unix socket 创建 |
| 进程/内核接口 | 独立 PID/IPC/user namespace、无能力、no-new-privileges；阻断 fork/clone、新 namespace、ptrace、io_uring、匿名文件和 SysV IPC 等接口 |
| 内存 | 内核硬限制 256 MiB 地址空间；不是仅靠 Python 对象计数 |
| 执行时间 | 每次大于 0 且至多 120 秒；父进程墙钟截止与 CPU 硬限制 |
| 输入 | Python 源码 64 KiB；初始/更新状态 JSON 每条至多 16 MiB |
| 输出 | 普通 Python 输出 256 KiB；协议单条 1 MiB、总计 2 MiB；stderr 64 KiB |
| 动作 | 每批至多 32 个，每次调用累计至多 128 个；动作结果以宿主回调返回为准 |
| 清理 | 正常结束、错误和超时均结束并回收隔离进程，关闭通信描述符 |

这些限额是新运行版的明确行为变化；超过预算会得到工具错误，而不是放宽隔离继续运行。模型任务应使用小批量动作和 JSON 返回值，不依赖写文件或创建线程/子进程。

宿主游戏动作回调是同步且可信的，它必须自行限制引擎调用时长。沙箱截止不会抢占正在执行的宿主回调；回调返回后会检查截止并保留已经发生的动作结果。这个边界不被包装成硬抢占能力。

## 可复核性

- `frozen-sha256.json` 固定 165 份冻结/历史资料，构建前核对文件集合和内容。
- `build_runtime.py` 只向仓库外的新目录构建，写入 `RUNTIME_PROVENANCE.json` 标明原始提交、反馈修复和覆盖文件哈希。
- `tests/test_isolation.py` 真正运行 Linux 隔离。前置条件缺失时测试失败，不跳过真实隔离验证。
- CI 只持有内容读取权限，并执行归档校验、真实隔离测试、冻结反馈回归、已安装依赖扫描和再次核对冻结资料。

冻结版本中的 Python 名称限制仍有已知缺陷；不要直接运行冻结版本来替代此入口。
