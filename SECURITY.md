# 安全边界与历史版本说明

## 2026-09-06 隔离修复

公开前审查复现了两份冻结 `python_tool_sandbox.py` 的越界文件读取：模型可访问的 Python 函数对象暴露了原始内建能力。独立工作目录、`-I -S` 和名称白名单不是操作系统级隔离。

`code/frozen-phase1/`、`code/frozen-phase2/` 和 `code/post-experiment/` 保留为历史证据，字节不变。它们不是已修复的部署入口。新的运行入口在 [runtime/README.md](runtime/README.md)，通过只读文件视图、namespaces、seccomp、硬资源限制和有界宿主协议建立独立边界。

### 威胁模型

保护对象：可信宿主上的工作区、用户目录、凭据、网络和其他进程。

不可信输入：模型生成的全部 Python 代码及其输出协议，包括绕过 Python 名称限制、直接调用系统库、伪造结果、异常大输出和无限循环。

可信组件：更新后的宿主 Linux 内核、系统 Python/共享库、Bubblewrap、libseccomp、宿主运行依赖、构建源文件以及游戏动作回调。操作者配置的 `DUCK_BWRAP`、Python 路径和源目录属于可信部署配置，不接受模型选择。

当前挂载的系统标准库与 x86-64 共享库目录可读；这些目录本身应只放系统运行文件，不存凭据。用户主目录、仓库目录及宿主 `/tmp` 不进入沙箱，传入的状态中也不应包含秘密。

在启用 Ubuntu userns 限制的宿主上，管理员需选择允许专用 Bubblewrap 应用创建隔离。仓库附带仅匹配 `/usr/local/libexec/duck-bwrap` 的 AppArmor 前置配置，CI 使用它；全局限制保留。运行库不自动修改系统策略，也不把此按应用前置条件冒充子进程安全策略。子进程的文件/网络/进程约束仍由强制 namespaces、seccomp 和硬限额执行。

不宣称覆盖：宿主或内核已失陷、可信库被篡改、硬件侧信道、GPU 服务隔离、外部游戏/模型服务安全，或可信动作回调自身的逻辑缺陷。进程地址空间与协议限额不是整台主机的总资源调度器；并发调用数量仍由宿主控制。

### 仍需注意的使用风险

- 历史 TAAF 代码包含 pickle 读取。仅对可信且未被篡改的内部缓存使用；不要导入不可信 pickle。加固 Python 工具不把宿主的任意反序列化自动变安全。
- 加固后的执行器不接受任意 Python 环境或默默降低隔离；不支持的平台会明确停止。
- 模型/游戏配置、API 请求日志、账单与总并发预算在宿主侧管理。此变更没有重新测量游戏分数或付费请求表现。
- 同步动作回调使用宿主自身的截止机制；它不是在任意位置可被安全抢占的线程。

## 来源与公开边界

该仓库收录基于 Tufa Labs 上游实现的研究快照；现有来源和代码声明保留，未把仓库所有内容重新指定为统一开源条款。代码可见性不等于对每份来源材料作出新的使用权声明。

报告新问题时，请先私下联系维护者，避免在公开讨论中粘贴真实密钥、用户文件或尚未处理的攻击细节。可先提供受影响提交、组件、预期边界和仅使用合成输入的复现摘要。

## 机制参考

- [Bubblewrap 的安全模型及责任边界](https://github.com/containers/bubblewrap#sandbox-security)
- [Bubblewrap 参数定义](https://github.com/containers/bubblewrap/blob/main/bwrap.xml)
- [libseccomp](https://github.com/seccomp/libseccomp)
- [Python pickle 的可信输入要求](https://docs.python.org/3/library/pickle.html)
