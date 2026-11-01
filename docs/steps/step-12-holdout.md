# STEP 12：P1-1 留出判据（`holdout_checks`）

上游：STEP 11（P1-3 无净进展，`467ee62` 及前 3 个 commit，已推，CI `41284552814` 绿）
commit：本批唯一提交 `feat: held-out 判据，不渲染进执行方提示词（P1-1）`

## 0. 直接回答

| 问题 | 答案 |
|---|---|
| "公开全绿才跑 holdout"在代码哪一行 | `verifier.py:923` `if task.holdout_checks and mechanical.ok:`（`mechanical` 在 `:916` 算、`:917` 落盘；`ok` 的定义是 `expectations.py:46` 的 `failed` 为空） |
| 执行提示词里 grep holdout | **0 命中**：`hidden-absent-file` → 0；`holdout-secret` → 0（见 §6.1） |
| 真跑里 `failure_reason` / `verdict` | `holdout-failed` / `None`（`state.json` 本轮条目，见 §6.4） |
| passed | **157 → 168**（11 条新增，见 §5） |

## 1. 范围

- 新增可选字段 `holdout_checks`（1–3 条），语义与 `checks` 相同。
- 不改既有 `checks` 语义，不改 P0-1 / P0-2 / P0-3 / P1-2 / P1-3 的既有行为，不加新命令、不加开关。
- 不 push。

## 2. 改动文件

| 文件 | 改动 |
|---|---|
| `schemas/task.schema.json` | 新增 `holdout_checks`：`minItems 1`、`maxItems 3`，item 结构与 `checks` 一致 |
| `src/agentgate/spec.py` | `TaskSpec.holdout_checks`；把 check 解析抽成 `_parse_checks(items, field=...)`，两处共用（重复名、`stdout_regex` 校验同一套） |
| `src/agentgate/collector.py` | `run_spec_checks(checks, worktree, timeout_s=...)`；`run_checks()` 变成它的一层薄包装 |
| `src/agentgate/holdout.py`（新） | `HoldoutFailure` / `HoldoutResult`、`evaluate_holdout()`、`output_tail()`；`as_document()` 落盘全量、`summary_document()` 只留序号与尾部 |
| `src/agentgate/verifier.py` | 门 `:923`、`_write_holdout()`、`_record_holdout_failure()`、`VerificationOutcome.holdout`；`_clear_previous_outcome` 把 `holdout.json` 一并清除 |
| `src/agentgate/orchestrator.py` | `verdict is None` 分支把 `verification.holdout` 抄进本轮状态（与 `no_progress` 同一个坑） |
| `src/agentgate/report.py` | `_holdout_lines()` 作为结论段主因；`_round_fact` 增加"留出判据未通过" |
| `src/agentgate/dryrun.py` | `DryRunResult.render()` 增加 `HOLDOUT: N checks (not shown)`；`build_execution_prompt()` **未改动** |
| `tests/test_holdout.py`（新） | 5 条端到端用例 |
| `tests/test_dryrun.py` | 2 条（不渲染 / 只报条数）+ 原有用例补一条"无该字段时没有 HOLDOUT 行" |
| `tests/test_spec.py` | 3 条（没有 checks 被拒 / 超过 3 条被拒 / 与 checks 并存能解析） |
| `tests/test_orchestrator.py` | 1 条：`ag run` 的内存态不能把 `holdout` 详情覆盖掉 |
| `docs/task-yaml-guide.md` | §0 可见性表、§1 引言、§1.9 顺序、新增 §1.11、§7 诊断行 |
| `docs/landscape.md` | §4 P1-1 改为终态语义并写明为什么不回灌 |
| `docs/borrowings.md` | P1-1 行与逐条说明；记录"原案是只进验收输入，落地改成终态" |

## 3. 语义

### 3.1 不进执行方可见的任何输出

- `build_execution_prompt()` 一行没改：它只渲染 `task.checks`。
- `build_defect_prompt()` 只渲染 defects，而留出失败**不产生 defect**。
- `--dry-run` 只打印条数：`HOLDOUT: 1 checks (not shown)`。
- 完整命令只落 `round-N/holdout.json`（本地工件）。`state.json` 里只存序号与输出尾部，
  所以连 state 被误贴出去也不会泄题。

### 3.2 失败即终态，不回灌

`verdict=None`、`failure_reason="holdout-failed"`、终态 `FAILED`（`ag run` exit 1）。
理由写在 `holdout.py` 模块 docstring 里：defect 的 `reproduce` 必须是可直接执行的命令，
填了就等于从第二轮起把留出判据交给执行方。

报告只写：

```text
留出判据未通过：#1（输出尾部 "AssertionError: holdout-secret.txt is missing"），未调用验收方。
```

`terminal_reason` 另有一句带轮次与尾部的英文说明（给"不建议合并；需先处理：…"用）：

```text
holdout-failed: round 1 did not clear its held-out checks (#1 AssertionError: holdout-secret.txt is missing)
```

### 3.3 只在公开判据全绿之后跑

顺序：`subject → HEAD 前向门 → protected → 公开机检 → 留出判据 → 无净进展 → 证据包 → 验收方`。

公开机检有失败时**完全不执行**留出命令（不是"执行了但忽略"）：`tests/test_holdout.py`
里那条用例让留出命令写一个标记文件，公开失败后断言标记文件不存在。

### 3.4 与无净进展的兼容

无净进展比的是**公开机检失败集合**（`verifier.py:962-964`）。留出判据只在
`mechanical.ok` 时运行，此时 `current_failed == ()`，与"上一轮失败集合非空"永远不等，
所以两者不会互相干扰；留出失败又是终态，不产生第二轮，也就没有基线可比。
现有 7 条无净进展用例全部原样通过（没有一条被改动）。

## 4. 门顺序（改动后行号）

```text
:873  subject = _frozen_subject(...) / _write_subject
:876  HEAD 前向门                        # 树动过 → stale-subject
:898  protected = _protected_gate(...)   # 命中 → BLOCKED
:916  mechanical = evaluate_checks(...)
:923  holdout（仅当 task.holdout_checks 且 mechanical.ok）
:926     失败 → _record_holdout_failure → FAILED（模型 0 次）
:956     通过 → 重取 before_status（见 §7 偏离 2）
:962  无净进展门
:996  evidence = collect_evidence(...)
:1021 selected = adapter or CodexAdapter()   # 验收方
```

## 5. 测试（11 条，157 → 168）

要求的 7 条：

| 用例 | 位置 | 断言要点 |
|---|---|---|
| `test_holdout_not_rendered_in_execution_prompt` | `test_dryrun.py` | 提示词里没有 `hidden-1/2` 与其 `run`，但公开 check 仍在 |
| `test_dry_run_hides_holdout_but_counts_them` | `test_dryrun.py` | `HOLDOUT: 2 checks (not shown)`，名称与命令都不在渲染结果里 |
| `test_holdout_failure_is_terminal_without_verifier` | `test_holdout.py` | `FAILED` 标记、无 `BLOCKED`/`DONE`、`attempts == ()`、模型 0 次、机检确认全绿 |
| `test_holdout_failure_reports_index_not_command` | `test_holdout.py` | 报告含 `#1` 与输出尾部，不含 check 名与命令 |
| `test_holdout_skipped_when_public_checks_fail` | `test_holdout.py` | 公开失败 → 留出命令**没被执行**（标记文件不存在）、无 `holdout.json` |
| `test_holdout_artifact_written_with_full_command` | `test_holdout.py` | `holdout.json` 里 `run` 与 task.yaml 一致、`exit_code=1`、stderr 有原文 |
| `test_no_holdout_field_behaves_as_before` | `test_holdout.py` | 不写字段 → PASS 照旧、无 `holdout.json`、state 里没有 `holdout` 键 |

额外 4 条（7 条没覆盖、但真实存在）：

| 用例 | 为什么加 |
|---|---|
| `test_holdout_without_public_checks_is_rejected` | 用户要求"只有 holdout 没有 checks → schema 报错"。实际报错来自 `checks` 的 required（`checks: required field is missing`），所以断言这句 |
| `test_holdout_is_limited_to_three` | `maxItems: 3` 是真约束，就得有用例证明它真的会拒 |
| `test_holdout_checks_parse_alongside_checks` | 证明两组能共存、`expect` 完整传下去 |
| `test_holdout_detail_survives_the_orchestrator_state` | 上一批踩过的坑：`ag run` 保存内存态会覆盖 verifier 写盘的内容。**移除 orchestrator 里那 3 行后该用例报 `KeyError: 'holdout'`**，已实证 |

## 6. 真跑证据

任务文件在**仓库外**：`D:/Projects/ag-tasks/p11-holdout.task.yaml`（含 holdout 的任务文件
按 §3 的风险约定不放仓库内）。公开 check 是 `findstr /x /c:"ok" holdout-probe.txt`；
留出 check 断言一个必然不存在的文件；`max_rounds: 2`。

### 6.1 执行提示词里搜不到留出判据

```text
$ grep -c "hidden-absent-file" runs/p11-holdout/round-1/executor_prompt.txt
0
$ grep -c "holdout-secret" runs/p11-holdout/round-1/executor_prompt.txt
0
```

`--dry-run` 的输出同理：`hidden-absent-file` 与 `holdout-secret` 都是 0 命中。

### 6.2 `--dry-run` 的那一行

```text
PROTECTED: none declared
HOLDOUT: 1 checks (not shown)
Evidence: 每个改动文件全文，单文件上限 32 KiB / 总量 128 KiB；…
```

### 6.3 运行结果与报告

```text
Round 1: PROTECTED: none declared
Round 1: holdout-failed → FAILED
PROTECTED: none declared
机检: 1/1 通过
FAILED + 1 轮 + 79.3s
```

`runs/p11-holdout/report.md`：

```text
# p11-holdout  FAILED  1.3min / 1 轮 / $0.0050 / 双盲 ★★

## 结论
留出判据未通过：#1（输出尾部 "AssertionError: holdout-secret.txt is missing"），未调用验收方。
PROTECTED: none declared
机检：1/1 通过。
R1 留出判据未通过。
最终分支仅新增 holdout-notes.md、holdout-probe.txt（+2）。
不建议合并；需先处理：holdout-failed: round 1 did not clear its held-out checks (#1 AssertionError: holdout-secret.txt is missing)。

## 每轮
- R1: holdout-failed
```

验收方调用次数：`ls runs/p11-holdout/round-1 | grep -c verifier-attempt` → **0**。
本轮目录里没有 `verdict.json`，终态标记只有 `FAILED`。

### 6.4 `holdout.json` 的键

```text
top:        ['checked', 'checks']
check keys: ['index', 'name', 'run', 'expect', 'exit_code', 'status', 'reason', 'duration_s', 'stdout', 'stderr']
run:        python -c "import pathlib,sys; p=pathlib.Path('holdout-secre...
exit:       1 FAIL
```

`state.json` 的本轮条目：

```text
status=failed  failure_reason=holdout-failed  verdict=None
holdout={'checked': 1, 'failed': [{'index': 1, 'name': 'hidden-absent-file',
         'tail': 'AssertionError: holdout-secret.txt is missing'}]}
terminal_reason=holdout-failed: round 1 did not clear its held-out checks (...)
```

这一组数字同时说明三件事：公开机检真的全绿（否则 holdout 不会跑）、留出判据真的
执行过并失败（`exit_code=1` + stderr 原文）、模型一次都没被叫（0 个 attempt 文件）。

## 7. 与提示词的三处偏离（都记在这里）

1. **原案说"结果只进验收输入"，落地改成终态不回灌。** 提示词 §3.2 本身就是这个要求，
   但 `landscape.md` 原案写的是另一句，两边冲突时按提示词改，并同步改 landscape。
   理由：`reproduce` 会泄题。
2. **`before_status` 在留出判据之后重取一次**（`verifier.py:956`）。留出命令和公开
   检查一样跑在 worktree 里，若它写了被 gitignore 之外的文件，不重取就会被
   `after_status != before_status` 误判成"verifier modified the worktree"，把一个正常
   的留出判据变成 BLOCKED。重取的是**模型漂移基线**，`subject` 仍绑定冻结的
   `diff.patch`，不受影响。
3. **`holdout.json` 纳入 `_clear_previous_outcome`**：`ag verify --force` 复验时先清旧工件，
   否则一次通过的旧结果可能被当成本轮结论读到。

## 8. 边界与未做的事

- 没有把留出判据塞进 `verifier_input.md`：验收方也是模型，虽然它只读，但没有理由让它
  看见留出命令。
- 没有为"任务文件放在仓库内"加代码拦截：worktree 里能不能读到任务文件取决于用户把
  文件放哪，代码无法在收集阶段判断；按约定写进文档（§1.11 风险段 + `tasks/README.md`）。
- 没有改 `ag verify` / `ag run` 的命令行接口，`FAILED` 仍是 exit 1、`BLOCKED` 仍是 exit 2。

## 9. 完成标志

- `uv run ruff check` → All checks passed
- `uv run ruff format --check` → 44 files already formatted
- `uv run pytest -q` → **168 passed, 2 subtests passed**
- `uv run ag doctor` → required 全 PASS，双盲档位 **★★★**；optional 的 DSH 探针
  `FAIL — 需 DEEPSEEK_API_KEY`（照实记录：这是本机没配 key，不是回归）
- `git status` → 只有本批改动，无临时文件
- 未 push
