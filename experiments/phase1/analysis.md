# LS20 四臂实验：离线补充分析

全槽已结束。

## ITT：保留全部指定槽位

| 臂 | 结束/分配 | 首关成功 | 平均 cap 分 | 成功局首关动作均值 | 首关投入动作均值 | 端到端均值/最大秒 | 时限内 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 2/2 | 1/2 | 0.4674 | 43 | 81.5000 | 981.0685/1185.0810 | 2 |
| probe | 2/2 | 0/2 | 0.0000 | — | 70 | 1185.2450/1185.5520 | 2 |
| belief | 2/2 | 2/2 | 1.8948 | 53.5000 | 55.5000 | 881.9180/1015.1020 | 2 |
| combined | 2/2 | 2/2 | 1.6470 | 32.5000 | 32.5000 | 1186.0295/1187.0240 | 2 |

首关动作均值仅针对成功局；首关投入动作均值包含失败局。cap 分沿用冻结运行器、当前局基线与整局完成权重上限。

## 进程退出、游戏结果与有效时间预算

`completed` 仅表示进程正常退出，不表示游戏完成。冻结 TAAF InlineTarget 会从 1770 秒内层预算扣除 min(600,1770/2)=600 秒收尾缓冲，因此软预算为 1170 秒（19.5 分钟），游戏初始化还会消耗该窗口的一部分；外层进程含工件收尾仍受 1800 秒上限约束。四臂规则相同。`cancelled` 计入内层预算终止，并与运行器 timeout 一起统计。

| 槽位 | 进程退出 | 游戏结果 | 预算终止类型 | 游戏 final_wallclock 秒 | 端到端秒 |
|---|---|---|---|---:|---:|
| r1-1-belief | completed | gave_up | — | 725.2971 | 748.7340 |
| r1-2-probe | completed | cancelled | inner_soft_budget_cancelled | 1161.2548 | 1185.5520 |
| r1-3-combined | completed | cancelled | inner_soft_budget_cancelled | 1162.2770 | 1187.0240 |
| r1-4-baseline | completed | cancelled | inner_soft_budget_cancelled | 1165.2072 | 1185.0810 |
| r2-1-probe | completed | cancelled | inner_soft_budget_cancelled | 1165.7758 | 1184.9380 |
| r2-2-baseline | completed | gave_up | — | 757.8895 | 777.0560 |
| r2-3-combined | completed | cancelled | inner_soft_budget_cancelled | 1164.2455 | 1185.0350 |
| r2-4-belief | completed | gave_up | — | 994.8119 | 1015.1020 |

预算公式来源：冻结 `tufa-arc-agi-framework/src/taaf/deploy_inline.py:108–110`；实际 soft_end_time 与 benchmark final_wallclock 已逐槽记录。

## 处理实际摄取与 protocol-qualified 描述子集

| 槽位 | probe 分配/介入 | belief 分配/事实注入请求 | 与分配匹配 P/B | qualified | 工具错误文本结果数 |
|---|---:|---:|---|---|---:|
| r1-1-belief | 0/0 | 1/42 | True/True | True | 9 |
| r1-2-probe | 1/82 | 0/0 | True/True | True | 0 |
| r1-3-combined | 1/24 | 1/62 | True/True | True | 2 |
| r1-4-baseline | 0/0 | 0/0 | True/True | True | 1 |
| r2-1-probe | 1/13 | 0/0 | True/True | True | 2 |
| r2-2-baseline | 0/0 | 0/0 | True/True | True | 0 |
| r2-3-combined | 1/56 | 1/84 | True/True | True | 2 |
| r2-4-belief | 0/0 | 1/94 | True/True | True | 4 |

qualified 定义：进程正常结束、1–120 动作、端到端不超过指定时限、恰好一个初始化事件、实际请求模型配置匹配；开臂须观察到对应处理（probe 真介入、belief 真事实注入），关臂须无对应观测。排除原因逐槽存于 JSON。
遵循指定时间预算而取消的游戏仍可符合该协议口径，并非成功游戏。该子集按处理后的观测筛选，仅作 protocol-qualified 描述，不是因果 per-protocol 估计。ITT 不因此删样本。

```json
{
  "baseline": {
    "assigned_n": 2,
    "finished_n": 2,
    "protocol_qualified_n": 2,
    "mean_first_level_success": 0.5,
    "mean_score_cap_1_15": 0.4674341342810786,
    "mean_level1_actions_spent": 81.5,
    "mean_elapsed_seconds": 981.0685,
    "first_level_successes": 1,
    "mean_first_level_actions_among_successes": 43,
    "max_elapsed_seconds": 1185.081,
    "within_time_limit_n": 2,
    "budget_termination_n": 1,
    "game_outcome_counts": {
      "cancelled": 1,
      "gave_up": 1
    },
    "probe_interventions": 0,
    "belief_fact_injection_requests": 0
  },
  "probe": {
    "assigned_n": 2,
    "finished_n": 2,
    "protocol_qualified_n": 2,
    "mean_first_level_success": 0,
    "mean_score_cap_1_15": 0.0,
    "mean_level1_actions_spent": 70,
    "mean_elapsed_seconds": 1185.245,
    "first_level_successes": 0,
    "mean_first_level_actions_among_successes": null,
    "max_elapsed_seconds": 1185.552,
    "within_time_limit_n": 2,
    "budget_termination_n": 2,
    "game_outcome_counts": {
      "cancelled": 2
    },
    "probe_interventions": 95,
    "belief_fact_injection_requests": 0
  },
  "belief": {
    "assigned_n": 2,
    "finished_n": 2,
    "protocol_qualified_n": 2,
    "mean_first_level_success": 1,
    "mean_score_cap_1_15": 1.8948274929211681,
    "mean_level1_actions_spent": 55.5,
    "mean_elapsed_seconds": 881.918,
    "first_level_successes": 2,
    "mean_first_level_actions_among_successes": 53.5,
    "max_elapsed_seconds": 1015.102,
    "within_time_limit_n": 2,
    "budget_termination_n": 0,
    "game_outcome_counts": {
      "gave_up": 2
    },
    "probe_interventions": 0,
    "belief_fact_injection_requests": 136
  },
  "combined": {
    "assigned_n": 2,
    "finished_n": 2,
    "protocol_qualified_n": 2,
    "mean_first_level_success": 1,
    "mean_score_cap_1_15": 1.6470127868739466,
    "mean_level1_actions_spent": 32.5,
    "mean_elapsed_seconds": 1186.0295,
    "first_level_successes": 2,
    "mean_first_level_actions_among_successes": 32.5,
    "max_elapsed_seconds": 1187.024,
    "within_time_limit_n": 2,
    "budget_termination_n": 2,
    "game_outcome_counts": {
      "cancelled": 2
    },
    "probe_interventions": 80,
    "belief_fact_injection_requests": 146
  }
}
```

## 描述性 2×2 效应

probe 主效应=(probe+combined−baseline−belief)/2；belief 主效应=(belief+combined−baseline−probe)/2；交互=combined−probe−belief+baseline。首关成功以比例计，分数以百分点计。每臂 n=2，不进行显著性检验。

```json
{
  "mean_first_level_success": {
    "probe_main": -0.25,
    "belief_main": 0.75,
    "interaction": 0.5
  },
  "mean_score_cap_1_15": {
    "probe_main": -0.3576244201641501,
    "belief_main": 1.537203072757018,
    "interaction": 0.21961942823385705
  }
}
```

## 请求耗时、provider 错误与 token

| 槽位 | 请求/错误/未收尾 | 已结束请求耗时 median/p95 秒 | completion 含 reasoning | reasoning | cached input | uncached input |
|---|---:|---:|---:|---:|---:|---:|
| r1-1-belief | 44/3/0 | 10.8046/33.1626 | 17423 | 6344 | 685440 | 341591 |
| r1-2-probe | 130/4/0 | 5.3155/18.1919 | 16370 | 4341 | 1364992 | 2314208 |
| r1-3-combined | 64/6/0 | 12.8013/44.3835 | 22938 | 15426 | 528064 | 1073931 |
| r1-4-baseline | 68/4/0 | 10.9459/33.6511 | 23562 | 13844 | 915584 | 767476 |
| r2-1-probe | 52/7/0 | 17.8190/61.2814 | 19178 | 11639 | 812288 | 189946 |
| r2-2-baseline | 37/2/0 | 13.0769/54.6540 | 14046 | 8579 | 488256 | 380669 |
| r2-3-combined | 88/5/0 | 5.3275/42.6802 | 17777 | 9015 | 451328 | 1993601 |
| r2-4-belief | 98/3/0 | 4.8165/28.6753 | 18701 | 8253 | 1705472 | 951871 |

全轮 usage 汇总：

```json
{
  "requests": 581,
  "responses": 547,
  "errors": 34,
  "unfinished_requests": 0,
  "provider_error_types": {
    "ReadTimeout": 32,
    "HTTPError": 2
  },
  "provider_error_http_status": {
    "None": 32,
    "429": 2
  },
  "latency_completed_requests_n": 581,
  "latency_median_seconds": 7.073087069002213,
  "latency_p95_seconds": 41.55293367300328,
  "latency_missing_terminal_records": 0,
  "tokens_all_observed_responses": {
    "prompt_tokens": 14964717,
    "completion_tokens_including_reasoning": 149995,
    "reasoning_tokens": 77441,
    "cached_input_tokens": 6951424,
    "total_tokens": 15114712,
    "uncached_input_tokens": 8013293
  },
  "tokens_known_field_subtotal": {
    "prompt_tokens": 14964717,
    "completion_tokens_including_reasoning": 149995,
    "reasoning_tokens": 77441,
    "cached_input_tokens": 6951424,
    "total_tokens": 15114712,
    "uncached_input_tokens": 8013293
  },
  "token_field_response_coverage": {
    "prompt_tokens": 547,
    "completion_tokens_including_reasoning": 547,
    "reasoning_tokens": 547,
    "cached_input_tokens": 547,
    "total_tokens": 547,
    "uncached_input_tokens": 547
  },
  "belief_fact_injection_requests": 282,
  "belief_fact_injection_rate": 0.4853700516351119,
  "distinct_injected_evidence_ids": 262,
  "request_model_settings": [
    {
      "model": "glm-5.3-flash",
      "provider": "bigmodel",
      "reasoning_effort": "low",
      "max_output_tokens": 8192
    }
  ]
}
```

耗时统计包括已结束 response/error 请求，p95 为 nearest-rank；未收尾请求单列，不赋造完成耗时。字段缺失保留 null，known-field subtotal 与覆盖数另存；completion 已含 reasoning，不二次相加。非缓存输入=prompt−cached。这些是已观测响应 token，不是账户总扣费，也不含失败请求可能发生的未报告消耗。

工具 error-text 按原始 transcripts 的单个 [TOOL RESULT: …] / [PYTHON TOOL RESULT] 段落计数，匹配 Traceback、error 标签或异常类文本；忽略模型思考及 viewer 中重复转录，不按同段重复打印累加。显示文本已丢失原始结构化错误标记，故该数不是已认证的 Python crash 数。
