# 证据索引与解释边界

## 结论优先级

1. 真实事件、benchmark、usage、冻结请求配置是相应测量的依据。
2. 每轮 `analysis.json` / `analysis.md` 是基于这些记录的离线分析。
3. [HANDOFF](HANDOFF.md) 汇总后续诊断与解释纠正。
4. `reports/` 为阶段报告，保留原先措辞并加归档纠正说明。
5. `background/` 是用户原始附件，不是新增实测；其中评分重算已被后续纠正。

## 判断与证据

| 判断 | 依据 | 边界 |
|---|---|---|
| 历史21动作、3.5714分 | historical benchmark、逐动作events | 单次；缺历史源码快照 |
| 第一轮belief实际送达 | phase1 usage请求的事实注入字段 | 送达不等于模型使用正确 |
| 第二轮预测先于动作 | phase2 events中的commitment和实际action | 核对格子而非全部规则文本语义 |
| 第二轮均分1.7857 | phase2全部8槽summary与benchmark | 不排除失败；n=2/组 |
| 局部预测覆盖1.79% | phase2 analysis与events中的检查和真实帧 | 不是完整世界模型准确率；像素覆盖不是信息价值 |
| stdout漏显与旧转移混淆 | CASE_EXCERPTS与冻结tool_agent | 已找到具体案例，不归因全部失败 |
| 改rule_id而非修订语义 | CASE_EXCERPTS与预测事件 | 命名版本不证明学习 |
| 短HTTP截止 | phase2 usage的timeout_seconds与冻结tool_agent | 本地预算与服务延迟混合影响 |
| 反馈补丁有效呈现执行事实 | post-experiment文件和新增回归 | 功能测试，不是新通关证据 |
| 新版相对历史是否退步 | 历史与新配置比较 | 多项变化未隔离，当前baseline非历史复现 |

## 数据目录的含义

每个正式槽位目录包含：

- `benchmark.json`：游戏结果、逐动作历史、关卡基线和累计成本。
- `run_config.json`：运行请求配置的脱敏副本。
- `artifacts/*events.jsonl`：前后画面、真实动作、分析、证据/预测事件。
- `artifacts/*usage.jsonl`：请求、响应、异常、时间、输入/缓存/输出/推理用量。

各轮顶层保留全部槽位的 summary 和分析；其中的本机 run_dir 已替换为占位路径。阅读时按槽位名定位仓库相对目录。

## 后续最需要隔离的变量

历史路由和配置复原程度；输出与输入预算；共享分析轮次的截止策略；反馈补丁；预测表示与反例修订；事实摘要；并发和服务时间窗口。

当前没有这些因素的完整单变量对照，也没有跨游戏泛化结果。后续对话应先提出最便宜、最能改变结论的检验，不把此资料包当成“已经证明世界模型有效”。
