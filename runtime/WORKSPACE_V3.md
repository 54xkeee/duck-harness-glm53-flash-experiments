# Compact Causal Workspace V3 — minimal runtime candidate

## Audit / provenance (2026-09-06)

User repository base: `1e5e18239defba534f28ebd20b945e02558c6075` (main, PR #1 merged).
Upstream main checked with Git: `Tufalabs/duck-harness` at
`7652836056c59e044f093e3c13ed7438c814169e`; inference tree
`9869737a84d945d939034a5be2e60d69aa097cba`. This repository is an exported research
archive, not a Git fork with upstream ancestry. Its inference source compared
against that upstream has only the nine existing experiment-modified/added files:
evidence_controller, prediction_controller, python_tool_sandbox, runtime_state,
tool_agent, framework/run, framework/solver, utils/openai_compat, utils/run_artifacts.
The remaining inference files match after line-ending normalization. No newer
upstream source update was available at audit time. Keep the existing feedback
and isolation fixes rather than replacing them with the upstream sandbox.

| Route | Main before this PR | This candidate |
|---|---|---|
| Compact Workspace | Model-written summary and optional belief digest | Separate bounded game/level/episode proposals, refreshed into every request independently of chat eviction |
| Evidence Store | Controller facts and experiment event logs | Host append-only request/attempt/transition/receipt JSONL, full before/after frames, replayable IDs and separate workspace file |
| Canonical rule identity | Model-selected rule ID | Fingerprint of structured subject/action/conditions/effect, no name field, contradiction survives identical resubmission |
| Dead-action suppression | Probe can interrupt a batch | Two observed exact-state no-effect attempts suppress further attempts; explicit retry_reason recorded |
| Option execution | Batches, old pixel/probe gates | Single Python call submits a short batch; host checks each real transition and stops remaining snippet on surprise |
| Host-owned action receipt | Strong feedback display only in prediction mode; runner already retains host callback results | V3 receipt before stdout/result, request/execution counts, fresh transition range, last real action and current step, including errors and zero-action calls |

## Enable and operate

Use the existing supported Linux build/isolation procedure in `README.md`.
Building remains offline and keeps all 165 frozen evidence files unchanged.
`build_runtime.py` writes overlay hashes and the checked upstream SHA into
`RUNTIME_PROVENANCE.json`. Integration uses exact single-match anchors and stops
on source drift; it does not edit the archive or monkey-patch a running process.

```bash
export DUCK_CAUSAL_WORKSPACE=1
export DUCK_PREDICTION_CHECK=0
export DUCK_VERIFIED_PROBE=0
export DUCK_BELIEF_STORE=0
export DUCK_MIN_REQUEST_SECONDS=20
```

Unset `DUCK_CAUSAL_WORKSPACE` for the existing baseline. The minimum request
window fix applies to both arms: do not dispatch HTTP when remaining episode
time is less than min(configured request timeout, minimum window). The total
episode/run deadline is still respected. No new paid model requests run here.

The Python tool accepts an optional `workspace` JSON argument:

```json
{
  "code": "result = causal_workspace",
  "workspace": {
    "game": {"controls": "candidate: arrows translate color 1"},
    "level": {"goal": "unconfirmed"},
    "episode": {"question": "does RIGHT translate the full color mask?"},
    "rules": [{"subject": "color:1", "action": "RIGHT", "conditions": {},
               "effect": {"color_translation": {"1": [0, 1]}}}]
  }
}
```

Use ordinary Python `action` calls for exploration and options:

```python
action([
    {"action": "RIGHT", "state_key": causal_workspace["state_key"],
     "expected": {"color_translation": {"1": [0, 1]}}},
    {"action": "RIGHT", "expected": {"color_translation": {"1": [0, 1]}}},
])
print(HOST_RECEIPT)
```

Option fields: `expected`, optional `state_key` (exact step precondition),
`rule_id` (must reference matching action/effect), `retry_reason` (intentional
no-effect re-probe). Predictions remain optional; the old prediction gate is not
required. Existing 32-action batch / 128-action tool / isolation limits remain.

Evidence lives beside the session state as `*.evidence.jsonl`, cognitive state as
`*.workspace.json`. Evidence is written only by host code; sandbox edits affect
copies. ToolAgent recreation at the same state path restores workspace/evidence.
Use a fresh session directory for each independent run. One host writer per state
path; concurrent writers and crash recovery of a partial final JSONL line are not
supported. Engine exceptions are reported as outcome unknown, never fabricated
as successful/zero-effect transitions. A process crash between engine action and
receipt persistence is not an exactly-once transaction with the game engine.

## Deliberate limits

- This is a local semantic-event verifier, not a universal object tracker. Color
  masks are only reported as translating when the entire occupied set matches a
  rigid shift; deformation/split/merge produces no translation claim. No guessed
  player identities, HUD masks, relations, resources or reward semantics.
- Rule identity is structural, not natural-language equivalence. Renaming a
  display label is rejected, but paraphrased subject/conditions can still differ.
  Conditions are recorded hypotheses, not executable predicates. `supported_local`
  means only the specified effect matched an observation, not validated mechanics
  or proof that the rule applies to a new state. Rule conditions require model
  reasoning; use exact `state_key` when a host-enforced precondition is needed.
- Dead-action equivalence includes the whole grid and level, not step count.
  Hidden timers can make visually identical states differ; retry_reason is an
  explicit, logged escape hatch. HUD changes reduce suppression rather than being
  guessed away. Any no-effect step stops the current option conservatively.
- Workspace is bounded to 6000 serialized characters and 16 rules; this is not a
  tokenizer-verified 800–1600-token guarantee. Four recent evidence summaries are
  injected separately. Over-budget proposals are rejected atomically.
- Game proposals/rules survive level changes or explicit RESET as priors;
  level/episode state and dead-action cache clear. No automatic game simulator,
  hypothesis-ranking optimizer, helper-code persistence or cross-game transfer.
- Baseline mode retains its existing output contract. The new receipt contract
  applies to the enabled V3 treatment. Mandatory receipts have priority over the
  configured output cap if an exceptionally small cap cannot fit the envelope.

## Verification and next experiment

Portable host unit tests:

```bash
python runtime/tests/test_causal_workspace.py
python runtime/build_runtime.py --verify-frozen
python tools/verify_archive.py
```

CI additionally executes real Linux isolation, existing feedback regressions,
new ToolAgent → isolated Python → fixture game → receipt tests, installed-package
audit, CLI import, and post-test frozen hashes. Integration fixtures cover a
two-step option, surprise/remaining-snippet stop, no-op suppression, zero-action
receipt, persistence into the next model request, syntax/output errors, child
receipt forgery, host timeout after a real action, and tiny HTTP budgets.

No new game performance is claimed. Before any paid trial, freeze actual provider,
model/effort, endpoint routing, seed/order, input code/config hashes and stop rules.
Proposed initial A/B: feedback/isolation/deadline-fixed baseline versus V3, old
prediction/probe/belief treatments off in both arms; four fresh LS20 sessions per
arm, randomized interleaving, identical 30-minute outer deadline and 1200 real
action ceiling. Keep every assigned run in ITT, including provider errors,
timeouts, zero actions and failed starts. Primary metrics: levels completed and
level-2 progress, then calls per executed action, wall time per completed level,
no-op recurrence, option length/surprise rate, token use and provider failures.
This is an unexecuted protocol proposal, not a frozen/started evaluation. A larger
sample and held-out games are needed for reliable or generalization claims.
