# 代码阅读入口

本目录是取证/理解用的实现资料，不是另一个完整部署发行版。上游是 [Tufalabs/duck-harness](https://github.com/Tufalabs/duck-harness)，原有声明保留，没有重新指定上游许可证。

**冻结 Python 执行器有已知隔离缺口，仅作历史取证。** 需要运行时使用仓库根目录的 [加固运行入口](../runtime/README.md)，它构建独立目录而不改本目录中的快照；详见 [安全边界](../SECURITY.md)。

## 版本边界

- `frozen-phase1/`：第一轮启动时本地保存的快照，覆盖范围小于第二轮。
- `frozen-phase2/`：第二轮冻结的 50 份源码、运行/分析 helper 和协议。第二轮成绩对应它。
- `post-experiment/`：第二轮之后修复反馈的当前文件，以及与第二轮工具代理的差异。
- 发布副本经过文本/路径脱敏；`MANIFEST.csv` 标出文本是否经过转换。原始本地冻结一致性检查发生在发布前，勿将此发布转换误认为正式实验中的代码变更。
- 历史 9 月 2 日没有完整源码快照。本目录不冒充那个版本。

## 建议阅读顺序

1. [prediction_controller.py](frozen-phase2/ARC3-Inference/inference/agent/prediction_controller.py)：预提交、条件、版本、反例、依赖和预测指标。
2. [evidence_controller.py](frozen-phase2/ARC3-Inference/inference/agent/evidence_controller.py)：真实位移证据与自动事实。
3. [tool_agent.py（冻结）](frozen-phase2/ARC3-Inference/inference/agent/tool_agent.py)：状态注入、Python执行、反馈包装、共享轮次时间预算。
4. [solver.py](frozen-phase2/ARC3-Inference/inference/framework/solver.py)：求解器与截止时间集成。
5. [python_tool_sandbox.py](frozen-phase2/ARC3-Inference/inference/agent/python_tool_sandbox.py)：模型代码到真实动作之间的边界。
6. [实验运行器](frozen-phase2/local/run_prediction_experiment.py) 与 [分析器](frozen-phase2/local/analyze_prediction_experiment.py)。
7. [反馈修复 diff](post-experiment/feedback-fix.diff)、[修复后 tool_agent.py](post-experiment/ARC3-Inference/inference/agent/tool_agent.py)、[反馈回归测试](post-experiment/ARC3-Inference/tests/test_prediction_io.py)。

新增反馈回归 7 项；本地完整套件从 106 到 113 项通过。这里收录的测试文件是相关集成测试，不声称整个113项运行环境都包含在此目录。当前上传过程只执行离线归档核对，不重跑模型实验。
