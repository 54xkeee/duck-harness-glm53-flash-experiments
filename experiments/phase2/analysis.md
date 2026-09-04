# GLM-5.3-Flash / LS20 预测闭环实验

八个指定槽位均已结束；全部保留在 ITT 分母。

## 主要游戏结果

| 臂 | n | 平均关卡 | 全游戏成功率 | 首关成功率 | 平均评分 / 100 | 平均动作 | 平均秒 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 2 | 0 | 0 | 0 | 0.0000 | 98.5000 | 1756.5430 |
| belief | 2 | 1 | 0 | 1 | 0.1324 | 210 | 1754.7080 |
| prediction | 2 | 0.5000 | 0 | 0.5000 | 1.7857 | 31.5000 | 1754.7090 |
| combined | 2 | 0.5000 | 0 | 0.5000 | 1.7857 | 18 | 1754.2065 |

| 槽位 | 进程 | 游戏状态 | 关卡 | 首关动作 | 总动作 | 评分 | 秒 |
|---|---|---|---:|---:|---:|---:|---:|
| r1-1-combined | completed | cancelled | 0 | — | 14 | 0.0000 | 1753.9140 |
| r1-2-belief | completed | cancelled | 1 | 236 | 236 | 0.0310 | 1753.9130 |
| r1-3-baseline | completed | cancelled | 0 | — | 149 | 0.0000 | 1756.5600 |
| r1-4-prediction | completed | cancelled | 1 | 21 | 49 | 3.5714 | 1753.9660 |
| r2-1-belief | completed | cancelled | 1 | 86 | 184 | 0.2337 | 1755.5030 |
| r2-2-prediction | completed | cancelled | 0 | — | 14 | 0.0000 | 1755.4520 |
| r2-3-baseline | completed | cancelled | 0 | — | 48 | 0.0000 | 1756.5260 |
| r2-4-combined | completed | cancelled | 1 | 13 | 22 | 3.5714 | 1754.4990 |

评分按全部真实动作重建：动作计入执行前所在关卡，RESET 本身及重置后的重玩成本均保留，完成关卡取全局高水位，与 TAAF GameRun 口径一致。reset_segments 仅解释重置，不挑最好一段。缺失评分依据时保留 null。进程 completed 与全游戏成功分开。

## 预测是否真正发生、验证与覆盖

| 槽位 | 行动前预测 | 已评分 | 动作覆盖率 | 全预测匹配率 | 预测变化格匹配率 | 仅静止格匹配率 | 实际变化格覆盖率 | 停止 / 拒绝 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| r1-1-combined | 12 | 12 | 0.8571 | 0.4167 | 0.3636 | 1.0000 | 0.0632 | 9/22 |
| r1-2-belief | 0 | 0 | 0.0000 | — | — | — | — | 0/0 |
| r1-3-baseline | 0 | 0 | 0.0000 | — | — | — | — | 0/0 |
| r1-4-prediction | 48 | 48 | 0.9796 | 0.7083 | 0.3636 | 1.0000 | 0.0119 | 15/28 |
| r2-1-belief | 0 | 0 | 0.0000 | — | — | — | — | 0/0 |
| r2-2-prediction | 13 | 13 | 0.9286 | 0.1538 | 0.1538 | — | 0.0418 | 12/18 |
| r2-3-baseline | 0 | 0 | 0.0000 | — | — | — | — | 0/0 |
| r2-4-combined | 22 | 22 | 1.0000 | 0.9091 | 0.9412 | 0.8000 | 0.0087 | 2/17 |

只将动作前已落盘且与实际动作记录一致的承诺计入精度。全预测包含明确预告的过关等边界预测；JSON 另列同作用域与边界两组。动作覆盖率分母为全部真实动作。实际变化格覆盖率分母为被评分动作中真正变化的格子，而非整屏背景。停止次数并非节省动作数，规则改版并非修复成功。修订后后续预测表现要求旧版已有反例且新版本首个验证动作之后仍有新的预提交验证。

## 请求故障与消耗

| 槽位 | 请求 / 响应 / 错误 / 未收尾 | 延迟 median / p95 秒 | 输入 token | 输出 token（含推理） |
|---|---:|---:|---:|---:|
| r1-1-combined | 107/93/14/0 | 9.3099/47.3313 | 2318798 | 34567 |
| r1-2-belief | 126/119/7/0 | 6.3578/32.3570 | 3617155 | 30835 |
| r1-3-baseline | 105/100/5/0 | 7.6564/41.9112 | 2775868 | 31748 |
| r1-4-prediction | 155/145/10/0 | 5.6581/41.8891 | 3551145 | 38766 |
| r2-1-belief | 105/97/8/0 | 9.9991/37.9137 | 2826063 | 35522 |
| r2-2-prediction | 111/99/12/0 | 8.8299/52.3963 | 2315610 | 43448 |
| r2-3-baseline | 100/88/12/0 | 10.2348/48.1394 | 2326262 | 31104 |
| r2-4-combined | 116/102/14/0 | 9.0681/42.8151 | 2771141 | 35245 |

请求异常按 HTTP 状态及异常类型在 JSON 单列。token 为已返回 usage 的观测量，缺失保留 null；推理 token 已包含在输出 token 内。运行失败照常进入主要 ITT，接口问题也单独解释，避免把故障等同于算法效果。

## 时间与比较范围

每槽外层硬上限 1800 秒，求解软预算 1740 秒，另留启动 30 秒和进程组清理 30 秒。新 launcher 使用独立每游戏预算，避开旧轮额外扣除 600 秒的问题；启动仍占外层窗口。
本轮每臂 2 次、单一 LS20，效应只作描述性对照。动作预算从旧轮 120 提升到本轮 1200，仅本轮四臂共享条件下的比较具备对照意义。事实注入、预测精度与通关分别报告，不以某一项替代整个游戏理解。

## 描述性 2×2 效应

```json
{
  "mean_levels_completed": {
    "prediction_main": 0.0,
    "belief_main": 0.5,
    "interaction": -1.0
  },
  "mean_full_game_success": {
    "prediction_main": 0.0,
    "belief_main": 0.0,
    "interaction": 0
  },
  "mean_score_cap_1_15": {
    "prediction_main": 1.7195260621433341,
    "belief_main": 0.06618822357095167,
    "interaction": -0.13237644714190341
  }
}
```

## 全轮请求汇总

```json
{
  "requests": 925,
  "responses": 843,
  "errors": 82,
  "unfinished_requests": 0,
  "provider_error_types": {
    "ReadTimeout": 80,
    "HTTPError": 2
  },
  "provider_error_http_status": {
    "None": 80,
    "429": 1,
    "500": 1
  },
  "latency_completed_requests_n": 925,
  "latency_median_seconds": 8.206365837000703,
  "latency_p95_seconds": 42.81512307200046,
  "latency_missing_terminal_records": 0,
  "tokens_all_observed_responses": {
    "prompt_tokens": 22502042,
    "completion_tokens_including_reasoning": 281235,
    "reasoning_tokens": 140143,
    "cached_input_tokens": 14067264,
    "total_tokens": 22783277,
    "uncached_input_tokens": 8434778
  },
  "tokens_known_field_subtotal": {
    "prompt_tokens": 22502042,
    "completion_tokens_including_reasoning": 281235,
    "reasoning_tokens": 140143,
    "cached_input_tokens": 14067264,
    "total_tokens": 22783277,
    "uncached_input_tokens": 8434778
  },
  "token_field_response_coverage": {
    "prompt_tokens": 843,
    "completion_tokens_including_reasoning": 843,
    "reasoning_tokens": 843,
    "cached_input_tokens": 843,
    "total_tokens": 843,
    "uncached_input_tokens": 843
  },
  "belief_fact_injection_requests": 438,
  "belief_fact_injection_rate": 0.4735135135135135,
  "distinct_injected_evidence_ids": 357,
  "request_model_settings": [
    {
      "model": "glm-5.3-flash",
      "provider": "bigmodel",
      "reasoning_effort": "low",
      "max_output_tokens": 8192
    }
  ],
  "prediction_state_injection_requests": 489,
  "distinct_injected_prediction_ids": 95,
  "provider_error_codes": {
    "None": 80,
    "1305": 1,
    "1234": 1
  },
  "api_failure_limit_hits": 0
}
```
