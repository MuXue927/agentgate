# STEP 14：`BLOCKED` 的机器可读原因码（P2-2）

上游：STEP 13（P1-4 状态写入，`60037d8`，已推，CI `41329504014` success）
commit：`feat: BLOCKED 原因码`

## 0. 直接回答

| 问题 | 答案 |
|---|---|
| 枚举有几种 | **10 种**：提示词给的 7 种 + 我加的 3 种（`verifier-mutated` / `budget-exceeded` / `checkpoint-failed`），理由见 §2 |
| 每种的产生点 | 见 §3 的表，逐条给 `文件:行` |
| 三条假后端测试耗时 | 见 [step-15](step-15-fake-backends.md) §3（2.73s / 2.62s / 1.60s） |
| 有没有既有测试因为加 code 变红 | 两条**一度红**，都在 src 侧修好（向后兼容措辞 + `getattr`），最终**没有改任何既有断言**。原因见 §6 |
| passed | **176 → 185**（阶段 A +6，阶段 B +3） |

## 1. 问题

`BLOCKED` 一词六义：执行进程崩了、验收进程崩了、模型输出解析不出来、模型自己说
"判不了"、执行方零改动、状态文件读不出来。报告里全是 `BLOCKED: <一句话>`，
人得读那句话才知道该重跑还是该改任务，脚本完全没法区分。

## 2. 枚举

`src/agentgate/blocked.py`：`class BlockedCode(StrEnum)`，十值。

提示词给的 7 个全部落地，另加 3 个，因为**真实存在产生点、而 7 个里没有能诚实地
套上去的**——硬套就是给一个错原因，比没有原因更坏：

| 追加的码 | 为什么不能套现有的 | 产生点 |
|---|---|---|
| `verifier-mutated` | 不是 `stale-subject`（HEAD 和工件都没变，是工作树脏了），也不是 `protected-path`（改的不是受保护路径） | 验收后 `after_status != before_status` |
| `budget-exceeded` | 不是后端也不是输出问题，是预算用完了；套 `backend-error` 会让"重试"变成错误建议 | `max_total_minutes` 超过，三处 |
| `checkpoint-failed` | `git commit` 在 worktree 里失败；这是本地环境问题，不是模型后端问题 | `_checkpoint_round` 抛错 |

`unreadable-state` 落地方式见 §5（它带来一处退出码变化，必须单独说）。

## 3. 每个产生点

| code | 触发条件 | 产生点 | 用例 |
|---|---|---|---|
| `no-changes` | 执行方 exit 0 但零改动（`NoChangesError`） | `execution.py:164` | `test_no_changes_carries_no_changes_code` |
| `backend-error` | 执行方进程非 0 | `execution.py:134` | 同上（下半段：`_drive` 转发） |
| `backend-error` | 执行方抛异常 | `orchestrator.py:467` | — |
| `backend-error` | 验收方进程非 0 | `verifier.py:1200` | `test_verifier_exit_nonzero_carries_backend_error_code` |
| `invalid-output` | 两次都拿不到合法判决 | `verifier.py:1237` | 既有 `test_two_invalid_outputs_block_without_verdict` |
| `invalid-output` | 上一轮 FAIL 却没有 defects 可回灌 | `orchestrator.py:439` | — |
| `verifier-blocked` | 模型自己判 BLOCKED（`ag verify`） | `verifier.py:1256` / `:1270` | `test_verifier_says_blocked_carries_verifier_blocked_code` |
| `verifier-blocked` | 模型自己判 BLOCKED（`ag run`） | `orchestrator.py:659` / `:667` | 见 §4 |
| `protected-path` | 改动命中 `protected`（M/D/R） | `verifier.py:714`（记录）/ `:1016`（结局） | `test_protected_hit_carries_protected_path_code` |
| `stale-subject` | HEAD 不是本轮记录的 commit | `verifier.py:993` / `:1000` | `test_stale_subject_carries_stale_subject_code` |
| `stale-subject` | 验收期间 HEAD 移动 | `verifier.py:1170` | 既有 `test_head_moving_during_verification_blocks` |
| `stale-subject` | 验收期间冻结工件变化 | `verifier.py:1188` | 既有 `test_frozen_patch_changing_during_verification_blocks` |
| `verifier-mutated` | 验收方改了工作树 | `verifier.py:1152` | 既有 `test_verifier_modification_invalidates_result` |
| `unreadable-state` | 轮次/运行状态文件坏或缺键 | `verifier.py:938` | `test_unreadable_state_carries_code_without_repairing` |
| `budget-exceeded` | `max_total_minutes` 超时 | `orchestrator.py:394`（轮前）/ `:485`（执行后）/ `:594`（验收后） | — |
| `checkpoint-failed` | `git commit` 失败 | `orchestrator.py:530` | — |

## 4. 设计：一个出口、一个必填参数

`verifier.py:559` `_mark_blocked(round_dir, round_no, reason, code)` 与
`:599` `_blocked_outcome(task_id, round_dir, round_no, reason, code, ...)`：
`code` 是**位置参数、无默认值**，所以"忘记传"在写代码时就暴露，不需要运行时兜底。
`_blocked_outcome` 是 `verify_round` 里唯一能不带判决退出的方式，八处 BLOCKED
结尾全部改用它，顺带把原来散在每处的 `VerificationOutcome(...)` 构造收了。

`orchestrator._finish(..., code=...)` 同样在 `status == "blocked"` 时强制要求 code，
缺失就抛 `OrchestratorError`——这不是默认值，是"调用点写错了"，宁可炸在测试里。

落盘：

- 轮次条目 `blocked_code`（`_mark_blocked` / `_record_blocked_round` /
  `_record_round_in_state` 三处，覆盖"有轮次条目"和"只有模型判决"两种情形）
- run 级 `terminal_code`（同一处 mutate 里写）
- marker 正文**不变**，第一行仍是人读句子（`_drive` 会把它读回来当 reason）

渲染：`report.md` 结论段首行 `BLOCKED [protected-path]: …`，每轮行
`- R1: BLOCKED [protected-path]`；`ag verify` 输出同一格式；CLI 退出码映射不变
（BLOCKED 仍是 2）。

## 5. 两处语义变化（都在预期内，但需要写下来）

1. **`ag run` 遇到模型判 BLOCKED 现在是终态**。以前它落到 FAIL 分支，
   defects 为空 → 下一轮一开头就是 `previous round failed without defect details`
   → 以 `invalid-output` 结束，等于让解析器背模型"明确拒判"的锅。
   现在 `orchestrator.py:651` 直接 `_finish(blocked, code=VERIFIER_BLOCKED)`。
   判决内容没变，变的是这个判决落到哪个终态。
2. **状态文件读不出来从"抛异常"改成"终态 BLOCKED"**（`verifier.py:938`）。
   批次 I 把它定为 `StateError` 抛出，`ag verify` 退出码 1；现在它是
   `BLOCKED [unreadable-state]`，退出码 2。理由：BLOCKED 的定义就是"环境问题，
   修好再来"，而 `resume` 那条路径仍然是抛错（命令拒绝执行 ≠ 一次 run 的结局）。
   文件本身一个字节都没动——仍然不重建、不猜测。

   这一条有个天然矛盾：**它不能把 code 写进那份读不出来的状态**。`_mark_blocked`
   在这里吞掉 `StateError`（只写 marker），code 走 outcome 与终端输出；`state.json`
   保持原样，**没有 `terminal_code`**。这是唯一一处"三处一致"不成立的地方，
   原因是物理上做不到，不是漏了。

   真实证据（不调模型，故意弄坏一份 run 的 state 后跑 `ag verify`）：

   ```text
   $ uv run ag verify tmp-unreadable        # runs/tmp-unreadable 复制自 p13-checks3，state.json 改成 '{"rounds": ['
   BLOCKED [unreadable-state]: Unreadable run state: D://Projects//agentgate//runs//tmp-unreadable//state.json (Expecting value: line 1 column 13 (char 12))
   exit=2
   $ cat runs/tmp-unreadable/BLOCKED
   Unreadable run state: D://Projects//agentgate//runs//tmp-unreadable//state.json (...)
   $ cat runs/tmp-unreadable/state.json
   {"rounds": [                              # 一个字节都没改
   ```

## 6. 既有测试：两条一度红，最终一条没改

| 测试 | 为什么红 | 怎么修 |
|---|---|---|
| `test_report.py::test_blocked_conclusion_snapshot_is_actionable` | 我给所有 BLOCKED 状态加了首行 `BLOCKED: <reason>`，而那条用例的状态是**批次 I 之前的旧格式**（没有 `terminal_code`），结论段多出一行 | 改 src：只有存在 `terminal_code` 时才加首行；旧 run 保持原措辞（否则等于给历史 run 编一个假原因） |
| `test_cli.py::TestCli::test_verify_blocked_is_exit_two` | `outcome.blocked_code` 在测试的 `SimpleNamespace` 上不存在 → `AttributeError` → 退出码变 1 | 改 src：`getattr(outcome, "blocked_code", None)`，与文件里既有的 `getattr(outcome, "failure_reason", "")` 同一写法 |

两条都不是"改断言让它绿"：第一条是真实的向后兼容问题（`ag report` 要能读旧 run），
第二条是渲染层不该假设对象一定有新字段。

## 7. 状态

- `pytest -q`：176 → **182**（阶段 A 完成后）；阶段 B 再 +3，全批 **185**。
- 两条 ruff 绿，`ruff format --check` 49 files 全通过。
- 未 push。
