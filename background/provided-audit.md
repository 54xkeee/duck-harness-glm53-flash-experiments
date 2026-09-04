> 用户提供的原始背景分析，未将原归档作为新增实验纳入。本文评分重算遗漏整局完成度上限，其19/21步高于3.5714的数值已被纠正；服务故障归因也需看后续诊断。ChatGPT内部引用不是本仓库文件。最终解释见 [HANDOFF](../docs/HANDOFF.md)。

已完成两份归档的安全解压、完整性核验、逐局重建、冻结代码审计、消融复算，以及与当前 Duck Harness 仓库和 ARC-AGI-3 评分规则的交叉验证。分析对象为你上传的两份原始归档。 

第一包内部 SHA-256 **344/344 通过**，消融包 **1140/1140 通过**；没有发现路径穿越或归档损坏。

# 核心结论

## Duck 最大的设计缺陷

> **Duck 把认识论写进了提示词，却没有把认识论编译成可验证的控制器。**

Duck 已经成功地把环境观察结构化：对象分割、哈希、邻接、历史、动作前后转移和结果元数据都做得很好。当前公开 prompt 也明确提供 `current_frame`、`previous_frame`、`history`、`transitions`，并有意隐藏原始数字网格、要求模型主要使用同色四连通分割；Python 调用则是每次 fresh。

但它没有继续把以下过程结构化：

- 什么才算有效证据；
- 哪条真实转移支持哪项机制；
- 假设何时可升级为“已确认”；
- 何时继续探测、何时停止；
- 何时允许批量动作；
- 何时值得建立世界模型；
- 何时提高推理强度；
- 出现反例后哪些知识必须失效。

因此，模型在同一时间扮演了**假设提出者、实验设计者、证据解释者、裁判、规划者和执行者**。这形成了一个自我验证闭环：提示词要求模型遵守科学方法，但系统没有独立判断它是否真的遵守。

你的消融非常清楚地暴露了这种漏洞。

# 三类运行真正说明了什么

## 1. Duck 参考局：动作效率极强，资源调度失败

Duck 在 LS20 首关用了 **21 动作**，人类基线是 22，冷启动动作效率几乎达到人类水平。

但它只完成 1/7 关，最终因上游服务超时取消。更关键的是：

- 21 个动作主要只对应 3 个产生大量 token 的模型决策阶段；
- 第 8 到第 9 动作之间单个思考/工具阶段约耗时 **970 秒**；
- 首关完成约耗时 1421.66 秒；
- 整局约 2132.80 秒后取消。

所以 Duck 在这一局的主要失败不是“动作浪费”，而是：

> **为了少走一步，允许单个推理分支占用十几分钟，最终失去了完成整局的机会。**

Duck 的设计正确地重视了 RHAE 中昂贵的真实动作，却相对低估了 wall-clock、provider timeout、模型请求不可恢复性和长尾延迟。它需要一个资源总督，而不只是“尽量少动作”的提示。

## 2. 206 动作 Pi 局：不是干净的 low-thinking 基线

这局大部分时间使用 low，你在接近首关完成时手动切到了 high。因此它不能解释为单一思考强度实验。

主要轨迹是：

- 开局误把白色“+”作为玩家候选；
- 真正可控的是橙蓝多色复合块；
- 在控制身份未确认前执行了 `DOWN × 5` 等低信息动作串；
- 第 77 动作完成首关；
- 此后又执行 129 动作仍未解决第二关；
- 曾把全黄死亡/失败动画误判为过关转场。

它暴露的是四个工程问题：

1. 对象身份没有通过最短可区分试验确认；
2. 批量动作没有逐步后置条件；
3. 缺少独立的死亡、过关、重置和中间动画状态机；
4. 大量自然语言推理没有沉淀为稳定、可验证的结构化知识。

## 3. 98 动作 Pi warm retry：证明记忆很值钱，但不是独立复现

这局是同 session warm retry，使用 high，并明确继承了上一局的机制和路线知识：

- 首关只用了 **13 动作**；
- 首关时间约 268.17 秒；
- 但过关后又用了 85 动作，仍未解决第二关。

这说明一旦控制体、规则和路径进入可用记忆，首关动作可以从 77 降到 13。真正瓶颈是：

> **第一次发现知识、验证知识、稳定保存知识以及按作用域跨关迁移知识。**

但 13 动作不能与 Duck 的 21 动作做冷启动公平比较，因为 warm run 不是独立样本。

# 消融实验的严格结论

## 四个实验臂都恰好是 1/2 成功

| 实验臂 | Probe | Ledger | 首关成功 |
|---|---:|---:|---:|
| A00 | 否 | 否 | 1/2 |
| A01 | 否 | 是 | 1/2 |
| A10 | 是 | 否 | 1/2 |
| A11 | 是 | 是 | 1/2 |

因此以“是否完成首关”为因变量：

- Probe 主效应：**0**
- Ledger 主效应：**0**
- 交互效应：**0**

报告中看似为正的分数效应，主要由 A11-r2 那个 **19 动作**离群优胜局驱动。n=2/臂时不能把它解释成机制已经验证。

## Ledger 实际上没有被测试

4 个被分配 Ledger 的运行中：

- A01-r1：没有调用；
- A01-r2：最后一个真实动作之后才调用；
- A11-r1：第 120 个、也是最后一个真实动作之后才调用；
- A11-r2：没有调用。

所以：

> **Ledger 在线 treatment uptake = 0/4。**

这不意味着 Ledger 无效，而意味着当前实验没有把 Ledger 作为真正的处理变量交付。它只是一个模型可以忽略的可选写作工具。

## Probe 存在两种相反失败

### 假确认：A10-r1

冻结执行层只验证以下字段是否非空：

- `confirmed.controllable`
- `confirmed.action_effect`
- `evidence`

没有验证 evidence 是否对应真实 transition，也没有验证对象是否真的按声明移动。

结果模型在第 3 动作把白色“+”错误自认证为可控玩家，然后解除单步限制。最终：

- 120 动作中 **110 次无影响**；
- 最长连续无影响 40；
- 最长同动作串 54；
- 只有 5 个独特 gameplay state；
- 首关失败。

这证明目前的 Probe gate 可以被错误自述轻易绕过。

### 探测仪式化：A11-r2

另一端是一直不确认：

- 120 个动作；
- 123 条 probe 记录；
- 292 个模型回合；
- 159 次 Python 调用；
- 没有完成基础控制自认证。

它却以 19 动作过了首关，说明强制单步可能确实保护了动作效率；但第二关仍继续高成本逐动作记录。由此得到的不是“Probe 已验证”，而是：

> **单步受控执行值得保留，但当前 probe 记录本身混合了有效实验和形式主义仪式。**

## 运行有效性也存在口径问题

原分析把 8/8 标为有效，因为只检查“最后真实动作是否发生在 30 分钟以内”。

若 30 分钟指从进程启动到结束和工件落盘：

- A01-r2：1849.35 秒；
- A10-r2：2014.66 秒，并有 external timeout；

则严格端到端有效数是 **6/8**。

另外，8 个接受运行共有：

- **97 次工具崩溃**；
- 8 次启动引号失败；
- 1 次 timeout handler 自身崩溃；
- 1 次零动作运行被排除；
- 1 个重复运行被 progress lock 排除。

其中许多工具错误来自冻结版本的接口人机工程，例如 `SimpleNamespace` 使用错误、缺少常见 builtins 和动作参数形状错误。当前 Duck main 已经修正了其中一部分，所以不能把全部 97 次都外推成当前仓库缺陷；但它们足以严重污染本次实验。

# 评分漂移

ARC-AGI-3 当前官方规则是：

\[
\text{level score}=\min\left(1.15,\left(\frac{\text{human actions}}{\text{AI actions}}\right)^2\right)
\]

然后按照关卡编号加权。官方 changelog 说明 cap 曾从 1.0 调整为 1.15。

因此：

- 19 动作首关：归档 **3.571429%**，当前应为 **4.107143%**；
- 21 动作首关：归档 **3.571429%**，当前应为 **3.919663%**；
- 36 动作：1.333774%；
- 77 动作：0.291545%；
- 112 动作：0.137801%。

这意味着跨实验比较必须同时保存：

- 原始持久化分数；
- scorer 版本；
- 公式哈希；
- 当前规则重算分数。

否则评分规则升级会被误解成 harness 性能变化。

# Duck 做得最好的部分

Duck 不应该被推倒重来。它的底层设计有几个明显强项：

1. **单一 Python 工具面**减少了通用 agent 的工具路由噪声。
2. **转移而非静态截图**是正确抽象：before/after、动作结果和历史语义很有价值。
3. **分割、哈希、邻接和包含关系**提供了有效的对象级 inductive bias。
4. **鼓励 BFS、搜索与可靠路线批处理**，能在规则确定后大幅减少动作。
5. **完整运行工件、viewer 和 reasoning trace**具有非常高的研究价值。
6. **21/22 的冷启动首关效率**证明这套最小架构确实具有竞争力。

Tufa Labs 自己也公开指出当前 harness 仍需要在 context management 和 perception 上改进。你的数据进一步说明：上下文问题实际上是“缺少证据感知压缩”，感知问题则是“缺少时序复合实体与精确查询”。

# 我建议的 Duck v2

## P0：首先修复认识论和可靠性

### 1. Evidence-backed belief store

每个真实动作产生不可变 `transition_id`。所有知识声明必须包含：

```json
{
  "predicate": "LEFT 使复合实体 e3 左移一格",
  "scope": "game",
  "status": "supported",
  "confidence": 0.71,
  "support_ids": ["t2", "t5"],
  "contradiction_ids": []
}
```

模型可以提出 claim，但只能由 verifier 根据真实差分把它升级为 verified。

### 2. 用 verifier 替代 self-certification

`basics_confirmed` 不能再由模型提交几个非空字符串解锁。至少应要求：

- 两条不同状态下的支持转移；
- 可控实体身份保持；
- 动作效果一致；
- 没有未解释的竞争变化；
- evidence ID 确实存在；
- 没有有效反例。

### 3. 显式控制器状态机

```text
ORIENT
→ IDENTIFY_CONTROL
→ INFER_DYNAMICS
→ INFER_GOAL
→ PLAN
→ EXECUTE
→ VERIFY
→ RECOVER / TRANSFER
```

模型不再需要每一步重新写一篇完整世界模型；系统根据验证条件控制阶段转换。

### 4. 批量计划、事务式执行

仍允许模型一次规划十几步，但执行器内部逐步执行：

- 每一步检查对象位置、资源、画面阶段和结果；
- 与预期不一致，立即取消剩余队列；
- 过关、死亡或重置立即切换状态机。

这样保留 Duck 的批处理优势，同时避免错误世界模型放大损失。

### 5. Resource governor

- 默认 low；
- 仅在规则冲突、平台期、高价值分支或跨关抽象时 high；
- 每次模型决策有 60–120 秒软截止；
- 超时保存 checkpoint；
- 接近总截止时禁止新建大型世界模型；
- 自动回退到已验证路径搜索或短 probe。

这直接针对 Duck 参考局中约 970 秒的单一决策阶段。

## P1：改进感知、记忆和工具接口

### 6. 时序复合对象追踪

当前同色四连通分割会把橙蓝 5×5 可控体拆开。应根据：

- 共同位移；
- 刚性几何关系；
- 同时出现、消失；
- 跨帧碰撞反应；

自动提出 multicolor composite entity。

### 7. 只读数字网格查询 API

不必把完整网格直接塞进模型上下文，但应让 Python 使用：

- `cell(row, col)`
- `crop(...)`
- `diff_cells(...)`
- `component_at(...)`
- `raycast(...)`
- `shortest_path(...)`

这样能保持上下文简洁，又允许精确模拟和验证。

### 8. Persistent audited workbench

保留 ephemeral sandbox，但增加经过测试、内容哈希化的持久工具库：

- 帧差分；
- 对象追踪；
- BFS/A*；
- 碰撞分析；
- HUD/资源解析；
- transition replay。

临时代码和经过验证的长期工具必须分开。

### 9. Evidence-aware compaction

不能只按时间删除最老消息。应该永久保留：

- verified claims；
- supporting/contradicting transitions；
- 被否定假设；
- 未解决问题；
- 当前计划；
- 最近少量原始上下文。

对于支持 retained reasoning 和 compaction 的模型/API，可以进一步保留原生 reasoning state；这种做法已被证明对长程工具任务有价值，但能否用于 GLM 取决于实际 provider 支持。

## P2：主动分配可执行世界模型

不应要求每局一开始就构建完整模拟器。更好的方式是根据：

- 剩余关卡数量；
- 当前规划分支；
- 规则冲突程度；
- 历史 replay error；
- 剩余时间和 token；

动态决定构建、修复、使用或绕过 world model。

近期 Tycho 将这个问题概括为 **active abstraction**：难点不仅是写出一个可执行模型，更是决定什么时候抽象值得它的成本。

# 下一轮实验最重要的改变

下一轮主 2×2 应改为：

- Mechanized verified probe controller：开/关；
- Automatically maintained verified belief store：开/关。

两个 treatment 都必须由控制器真正执行，而不是一个强制、一个可选。

建议：

- 至少 10 个游戏；
- 每臂每游戏 5–10 个 fresh replicates；
- 按游戏和 provider 时间窗口分块；
- 交错随机运行顺序；
- 完整进程 30 分钟硬截止；
- output、reasoning、cache-read token 分开；
- 同时报告 ITT 和经过认证的 per-protocol；
- provider timeout 和 tool crash 保留在主分析中；
- 先用 scripted oracle 验证测量仪器，再正式跑模型。

# 交付文件

完整分析包：

:chatgpt-content-reference{index="8"}

:chatgpt-content-reference{index="9"}

主要文档：

:chatgpt-content-reference{index="10"}

:chatgpt-content-reference{index="11"}

:chatgpt-content-reference{index="12"}

数据与实现：

:chatgpt-content-reference{index="13"}

:chatgpt-content-reference{index="14"}

:chatgpt-content-reference{index="15"}

:chatgpt-content-reference{index="16"}

:chatgpt-content-reference{index="17"}

:chatgpt-content-reference{index="18"}

:chatgpt-content-reference{index="19"}

:chatgpt-content-reference{index="20"}

分析包 ZIP 的 SHA-256 为：

```text
2d1c6a8203702055a2fee53d6949f795dfd0e59b26223974d6ce3797ba8ced88
```