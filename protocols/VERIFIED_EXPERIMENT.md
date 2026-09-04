# GLM-5.3-Flash 四臂运行器

Windows 入口（提示输入密钥，输入内容不回显；结束后恢复原进程环境）：

```powershell
& '<SOURCE_ROOT>\local\run_verified_experiment.ps1'
```

在 WSL Ubuntu-24.04 中使用现有 Python 环境运行。凭据仅从进程环境读取，命令行不含凭据：

```bash
<LINUX_HOME>/.venvs/duck-harness/bin/python \
  '<SOURCE_ROOT>/local/run_verified_experiment.py' \
  --output '<SOURCE_ROOT>/local/verified-ls20-20260904' \
  --jobs 2
```

运行前设置 `LOCAL_ANALYZER_API_KEY`、`LOCAL_ANALYZER_BASE_URL`。本轮 endpoint 为 `https://open.bigmodel.cn/api/paas/v4/`。模型固定 `glm-5.3-flash`，provider 固定 `bigmodel`，thinking 开启、reasoning effort `low`、最大输出 8192、请求超时 90 秒。多模态统一 `current_grid`、放大 16。其余公用配置由运行器固定并记录在 `summary.json` 的非密钥白名单中。

默认实验：LS20、每臂 2 次、每局 120 动作；两开关 `DUCK_VERIFIED_PROBE` 和 `DUCK_BELIEF_STORE` 形成 baseline/probe/belief/combined。每局创建独立进程和目录；每个 replicate 内按种子 20260904 随机顺序启动四臂，当前 replicate 全部结束后进入下一个。该种子控制顺序与 Python hash，并非宣称远端模型确定性。

每槽从创建目录前开始计时，总预算最多 1800 秒；默认 1770 秒进入进程组终止流程，5 秒后强制结束，并用余下 25 秒完成工件读取与结果写入。正常退出的组也清理遗留子进程。运行结果记录实际耗时与 `deadline_exceeded`。请求错误、启动失败、超时、零动作都保留在 ITT 分母中。输出目录须为全新路径，避免覆盖或复用游戏上下文。

输出 `summary.json`、`summary.csv`、中文 `report.md`、`source_snapshot`，每槽另有 `slot.json` 和脱敏 `process.log`。读取现有 `artifacts/*_events.jsonl` 和 `artifacts/*usage.jsonl`，容忍中断留下的尾部半条 JSON。按逐动作重建统计，当前 `benchmark.json` 提供人类基线；缺失基线且已过关时重算分留空。整局评分同时应用单关 1.15 上限和完成关卡权重上限。

已有结果可离线刷新，过程不调用模型：

```bash
<LINUX_HOME>/.venvs/duck-harness/bin/python \
  '<SOURCE_ROOT>/local/run_verified_experiment.py' \
  --output '<SOURCE_ROOT>/local/verified-ls20-20260904' --summarize-only
```

离线验收：

```bash
<LINUX_HOME>/.venvs/duck-harness/bin/python -m unittest discover \
  -s '<SOURCE_ROOT>/local' -p test_verified_experiment.py -v
```
