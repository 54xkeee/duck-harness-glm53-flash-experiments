# LS20 预测闭环实验预注册协议

冻结日期：2026-09-04。本文在正式实验开始前确定。

## 问题与四臂

检验两个功能分别及组合是否改善游戏表现：自动事实记录（belief）与动作前预测、真实状态验证和失配停止（prediction）。旧版仅靠运动一致性的 probe 全部关闭。

| 臂 | DUCK_BELIEF_STORE | DUCK_PREDICTION_CHECK | DUCK_VERIFIED_PROBE |
|---|---:|---:|---:|
| baseline | 0 | 0 | 0 |
| belief | 1 | 0 | 0 |
| prediction | 0 | 1 | 0 |
| combined | 1 | 1 | 0 |

每臂 2 次，合计 8 个独立进程；每局全新游戏及模型上下文，无上一局路线或预测库注入。按 replicate 分为两个区组，区组内用固定随机种子 20260905 洗牌，区组间不交叠，最多并行 2 局。这个种子仅决定顺序和 Python 哈希种子，未声称控制 provider 采样随机性。

## 共同预算和模型设置

- 游戏 LS20，development live arcade；不是向比赛服务器提交成绩。
- 官方 GLM-5.3-Flash，`reasoning_effort=low`、最大输出 8192、上下文 65536、温度 0.6、top_p 0.95。
- 请求时限 90 秒、Python 工具时限 30 秒；当前画面输入，图像放大 16 倍。
- 所有臂共用 `DUCK_API_FAILURE_LIMIT=3`；同一会话连续 3 次 API 错误且无成功响应时结束当前会话，下一槽调度前再判断是否暂停队列。
- 每局真实动作上限 1200；全流程硬上限 1800 秒。
- 启动预留 30 秒、求解预算 1740 秒（29 分钟）、清理预留 30 秒。外层自进程启动 1770 秒发送 TERM，随后 KILL 整个进程组，清理仍处于 1800 秒窗口。
- 新 launcher 传 `--max-runtime-minutes 29`，省略整实验时长覆盖项；现有框架为 inline 自动加 600 秒，再由 InlineTarget 扣除 600 秒，求解软预算为 1740 秒。实际启动或文件收尾若超出预留，外层硬上限优先。
- 与上一轮相比，动作预算从 120 增至 1200，时间扣减问题也已修正。因此主要对照仅在本轮四臂之间，旧轮只作背景。

正式八槽之前完成单独 API 连通与图像工具测试；预检数据不进入八槽分母。运行开始前一次性复制 inference、TAAF Python 源码、新运行器、分析器、导入的旧辅助脚本、测试及本协议到 source_snapshot。八槽运行期间代码保持冻结。

## 接口故障与中断

每局的失败、超时、零动作和正常退出均按原分配保留，不因表现差而重跑。HTTP 401/402/403 或明确的余额、配额错误停止后续排队任务；单次 HTTP 429 只计限流，不自动当作余额不足。

运行器在槽位完成后检查结构化请求记录；在完成槽顺序中出现连续 3 次错误且中间没有正常响应时，暂停剩余队列。已经运行的槽位继续收尾。未启动的槽位保持 pending；暂停状态不算八槽完成，也不给待运行槽位补零分。恢复或重跑需另行明确记录，不静默替换失败样本。

## 主要结果：全部指定槽位 ITT

1. 完成关卡数。
2. 全游戏成功比例。
3. 逐动作重建的 ARC-AGI-3 分数。
4. 首关成功比例、首次成功动作数、总动作数、端到端耗时作为辅助结果。

评分采用本地已核对的 TAAF GameRun 口径：完成动作计入执行前关卡；RESET 动作和重玩成本均累计在对应关卡；完成关卡用本局高水位。逐段 reset_segments 仅用于解释过程，不取最好一段。按当前局的人类动作基线重算，缺失且已经过关时保留未知，并与覆盖全部动作的 benchmark 比较。

令第 i 关人类基线为 h_i、全部投入动作为 a_i、过关指示为 c_i：

`s_i = c_i ? min(1.15, (h_i/a_i)^2) : 0`

`score = 100 * min(sum(i*s_i), sum(i*c_i)) / sum(i), i=1..L`

进程 `completed` 仅代表正常退出；游戏通关单独记录。八槽全部达到终止状态后才输出最终分臂结论。

## 机制指标：记录与理解分开

- 行动前已落盘的预测承诺数、真实动作验证数、预测覆盖全部真实动作的比例。
- 主表报告全部有效预提交预测的匹配率；同作用域和边界预测在 JSON 分层，正确预测过关属于有效的边界预测。
- 预测变化格与仅静止格分别报告；并计算预测覆盖了多少真正变化的格子，防止靠背景不变制造高分。
- 失配、前置条件失败、失效依赖、无预测探索和边界停止分别计数。停止次数不是节省动作数。
- 规则版本创建与修订数；旧版出现反例后，新版首个检验动作之后继续发生的预提交验证单独计数。修订文字本身不是修复成功证据。
- 事实实际注入请求、HTTP 状态、请求延迟 median/p95、超时和 token 消耗单独报告。
- 以结构化日志为准；工具输出中的错误文本只作为诊断，不当作游戏理解指标。

样本量为每臂 n=2、单游戏，只报告描述性均值、比例与 2×2 主效应/交互，不作显著性宣称。数据齐全和配置匹配是观测检查，不按这些事后指标删掉 ITT 样本。

## 执行与产物

在 WSL 的已配置 Python 环境运行；API key 通过环境传入，不进入脚本或报告。

```powershell
wsl.exe -d Ubuntu-24.04 -- bash -lc 'cd "<SOURCE_ROOT>/local" && <LINUX_HOME>/.venvs/duck-harness/bin/python run_prediction_experiment.py --output "<SOURCE_ROOT>/local/prediction-ls20-20260904"'
```

正式输出目录必须全新。运行状态在 summary.json、summary.csv、report.md；完整结束后自动生成 analysis.json、analysis.md。每槽保留逐动作事件、预测承诺、usage、transcripts、benchmark 和已脱敏 process.log。

离线复算：

```powershell
wsl.exe -d Ubuntu-24.04 -- bash -lc 'cd "<SOURCE_ROOT>/local" && <LINUX_HOME>/.venvs/duck-harness/bin/python analyze_prediction_experiment.py --output "<SOURCE_ROOT>/local/prediction-ls20-20260904"'
```
