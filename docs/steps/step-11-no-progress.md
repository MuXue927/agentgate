# STEP 11：小锈（doctor 阈值 + clean 文案）+ P1-3 无净进展

上游：STEP 10（P0-2 判决绑定，`83df219` / `0c84276`，已推，CI `41217386943` 绿）
commit：`5a5beef`（阶段 A）、`fe8de2d`（阶段 B）

## 0. 四个问题的直接回答

| 问题 | 答案 |
|---|---|
| 无净进展检测在代码哪一行？ | `src/agentgate/verifier.py:793-794`（`previous_sha = _previous_diff_sha(...)` / `if previous_sha and previous_sha == subject.diff_sha256:`），记录函数 `_record_no_progress` 在 `:607`，读上一轮哈希的 `_previous_diff_sha` 在 `:584` |
| 相对 protected 与机检的先后 | subject 前向门 `:770` → **无净进展 `:793`** → `_protected_gate` `:824` → `evaluate_checks` `:842` → 验收方 `:871` 起 |
| `verify_round` 全路径证据里模型调用次数 | **0**（`test_identical_diff_fails_without_verifier` 断言 `calls == []`，桩函数一旦被调用就 `AssertionError`） |
| doctor 改后跑一次 | 23:59:27 起，00:00:38 止，**71s**，档位 **★★**（验收方探针 18.69s 一次通过；DSH/Pi 仍缺 key） |
| passed | **144 → 153** |

## 1. 范围

阶段 A（两处小锈）+ 阶段 B（P1-3）。不做 P1-1、P1-4、P1-5；不改 `--force`
语义；不动 `wt/tmp-evidence-probe-r1` 残骸，也不为它写清理逻辑；不加新命令、
不加新开关。

## 2. 改动文件

| 文件 | 改动 |
|---|---|
| `src/agentgate/doctor.py` | 默认验收方探针 `timeout_s` 45 → 75，并给该探针 `attempts=2`（其余探针不变） |
| `src/agentgate/workspace.py` | 新增 `_permission_hint()`；`git worktree remove` 失败且 stderr 指向权限问题时追加管理员三步命令 |
| `src/agentgate/verifier.py` | `VerificationOutcome` 增 `failure_reason`；新增 `_previous_diff_sha`、`_record_no_progress`；`verify_round` 插入无净进展门并重排 protected/机检的先后 |
| `src/agentgate/orchestrator.py` | `verdict is None` 分支区分 FAILED（有 `failure_reason`）与 BLOCKED（无），前者写 `_finish(..., "failed")` |
| `src/agentgate/report.py` | 结论段 `_no_progress_lines`（主因 + 至多一条附加）；每轮行 `R<N>: no-progress`；每轮事实 `R<N> 无净进展` |
| `src/agentgate/cli.py` | `ag verify` 遇 `failure_reason` 打 `FAILED: …` 并 exit 1（BLOCKED 仍 exit 2） |
| `tests/test_verifier.py` | 4 条 P1-3 用例 + `add_round`/`write_run_state` 夹具 |
| `tests/test_report.py` | 1 条：无净进展压过同缺陷指纹 |
| `tests/test_cli.py` | 1 条：no-progress 退出码 1 |
| `tests/test_doctor.py` | 1 条：只有验收方探针有重试预算 |
| `tests/test_clean.py` | 2 条：权限提示出现 / 无关失败不出现 |

## 3. 阶段 A

### 3.1 探针 45 → 75，只给这一个探针加 attempts

原证据：17:28 同一条命令 45.28s 超时 → `unavailable`；17:29 13.08s 通过 → `★★★`。
45s 卡在免费档 p95 上，档位变成掷硬币。

改法：`timeout_s=75` + `attempts=2`（最坏 150s）。**只给 `verifier_codex`**：
`executor_codex` 保持 `attempts=1`，有测试盯这条，防止将来"顺手统一加重试"
把每次 doctor 的耗时翻倍。

### 3.2 clean 的失败文案：只加字，不加逻辑

触发条件不是所有删除失败，而是 stderr 里出现
`directory not empty` / `permission denied` / `access is denied` 之一。命中才追加：

```text
无法删除 <path>：权限异常（可能是执行方沙箱遗留的不可读目录）。
需要管理员终端执行：
  takeown /F "<path>" /R /D Y
  icacls "<path>" /reset /T /C
  rd /s /q "<path>"
```

不命中就保持原样（例如 `'wt/x' is a main working tree` 这种正常报错不会多嘴）。
**没有自动清理**：`wt/` 是 gitignore 的运行时目录，为一次性 ACL 事故加代码不划算。
维护者提示词里那条写成了 `icacls ... /T /C rd /s /q ...` 一行，实际是三条独立命令。

## 4. 阶段 B：P1-3

### 4.1 复用 `subject.diff_sha256`，不重新算哈希

`_previous_diff_sha()`（`verifier.py:584`）读 `runs/<id>/round-<N-1>/subject.json`
的 `diff_sha256`。P0-2 已经把这一轮的冻结 diff 哈希算好写在那里，这里再算一遍
就是给将来的格式变更埋一个"两处说法不一致"的雷。

代价：**P0-2 之前的老 run 没有 `subject.json`，因此永远不会被判无净进展**——
宁可漏判也不误判，而且这些 run 本来也已经结束了。

### 4.2 门顺序：subject → 无净进展 → protected → 机检 → 验收方

```text
:767  subject = _frozen_subject(...)          # 写 subject.json
:770  recorded_head 前向门                     # 树动过就 stale-subject
:793  previous_sha == subject.diff_sha256 ?    # ← 本批新增，命中即 FAILED
:824  protected = _protected_gate(...)
:842  mechanical = evaluate_checks(...)
      验收方
```

diff 一个字节都没变时，protected 与机检的结论必然与上一轮相同，跑一遍纯属复读；
此时再调验收方，等于拿同一份证据问第二次——和 P0-2 要解决的"冻结工件"是同一类
问题。所以命中时：**protected 不跑（`protected.json` 不产生）、机检不跑、
验收方 0 次**。

实现上把 `_protected_gate` 的计算整体挪到门之后（不只是挪它的判定），
`changes` 仍在门之前读——它后面给证据包用，早读一次不算"跑了 protected"。

### 4.3 FAILED 而不是 BLOCKED

`VerificationOutcome` 增 `failure_reason`：空 = BLOCKED（环境原因，人可以修），
`"no-progress"` = FAILED（一次真实结局）。配套三处：

- `orchestrator`：`verdict is None` 且有 `failure_reason` → `_finish(..., "failed")`，
  推送 `Round N: no-progress → FAILED`；
- `cli verify`：`FAILED: no-progress — …`，exit **1**（BLOCKED 仍 exit 2）；
- 状态写盘：轮次 `status=failed`、`failure_reason=no-progress`、
  `no_progress={previous_round, diff_sha256}`、保留 `subject`，
  清掉 `verdict`/`mechanical`/`defect_fingerprints`（与 BLOCKED 同一套清理规则）。

### 4.4 报告：一个主因，至多一条附加

结论段第一行：

```text
无净进展：R2 的 diff 与 R1 完全相同（sha256 0fa35566），未调用验收方。
```

"同缺陷连续两轮"若也成立，写成紧随其后的一条 `附加：…，但这不改变主因。`。
每轮行写 `- R2: no-progress`。既有"同缺陷"逻辑本身一行未改。

## 5. 测试

```text
$ uv run pytest -q
153 passed, 2 subtests passed in 78.31s
```

五条要求的用例：

```text
$ uv run pytest -v tests/test_verifier.py::test_first_round_is_never_no_progress \
    tests/test_verifier.py::test_identical_diff_fails_without_verifier \
    tests/test_verifier.py::test_changed_diff_still_goes_to_verifier \
    tests/test_report.py::test_no_progress_outranks_repeated_defect \
    tests/test_verifier.py::test_no_progress_artifact_has_no_stale_verdict
tests/test_verifier.py::test_first_round_is_never_no_progress PASSED      [ 20%]
tests/test_verifier.py::test_identical_diff_fails_without_verifier PASSED [ 40%]
tests/test_verifier.py::test_changed_diff_still_goes_to_verifier PASSED   [ 60%]
tests/test_report.py::test_no_progress_outranks_repeated_defect PASSED    [ 80%]
tests/test_verifier.py::test_no_progress_artifact_has_no_stale_verdict PASSED [100%]
5 passed in 7.59s
```

| 用例 | 断言的是 |
|---|---|
| `test_first_round_is_never_no_progress` | R1 走模型 1 次、PASS、`failure_reason == ""` |
| `test_identical_diff_fails_without_verifier` | 入口是真实 `verify_round(round_no=2)`；`calls == []`（桩被调用即抛 `AssertionError`）、`failure_reason=no-progress`、`attempts == ()`、`FAILED` 文件在、`BLOCKED` 不在、state 轮次 `failed` 且 `subject.diff_sha256` 与 R1 相同 |
| `test_changed_diff_still_goes_to_verifier` | R2 diff 多一行 → 模型 1 次、PASS |
| `test_no_progress_outranks_repeated_defect` | 两者标记同时在 → 结论第一行是无净进展，附加细节在其后（`index` 比较） |
| `test_no_progress_artifact_has_no_stale_verdict` | 预置旧 `verdict.json`/`mechanical.json` → 跑完两个文件都不存在，`subject.json` 在，state 无 `verdict`/`mechanical` 键 |

另有 3 条与阶段 A 相关：探针预算、权限提示出现、无关失败不提示。1 条 CLI：
`no-progress` 退出码为 1 而不是 2。

### 5.1 夹具怎么造出"两轮 diff 完全相同"

`add_round()` 把 `round-1` 的 `artifacts/`、`changed_files.txt`、`state.json`
复制成 `round-2`，`diff.patch` 原样复制（或按 `diff_extra` 改）。同时删掉
`DONE`（真实 orchestrator 只在整轮结束时写它），并把 run state 的
`head_commit` 写成当前 `HEAD`——否则先撞上 stale-subject 门，测的就不是无净进展。

## 6. 证据：真跑能做什么、不能做什么

### 6.1 真跑两轮（`runs/p13-signal`，模型真调用）

任务：goal 要求 `p13-probe.txt` 内容为 `ok`，check 却要求 `ok2`。R1 机检必挂，
R2 执行方按缺陷反馈改成 `ok2`（机检过、目标违），最终 `max_rounds` 耗尽。

```text
$ uv run ag run …/ag-p13.task.yaml --allow-self
Round 1: 机检未通过：probe-exact
Round 1: FAIL (1 defect) → continue
Round 2: PROTECTED: none declared
Round 2: FAIL (1 defect) → FAILED
机检: 1/1 通过
Verdict: FAIL（验收官）— Goal not satisfied: p13-probe.txt contains 'ok2' instead of required 'ok'
FAILED + 2 轮 + 264.1s
```

两轮的真实哈希（`runs/p13-signal/round-{1,2}/subject.json`）：

```text
R1  diff_sha256 7e9260d31b8ac2d58837879d620b3e295409de859f97e4c5baa46e116ed827b7   (+ok)
R2  diff_sha256 e19703b3cedb5424010f626c22793e4d2c8e37e22e60b95c956dbf26fe5a71f   (-ok +ok2)
```

**不同 → 检测未触发 → R2 照常调用验收方**（`round-2/verifier-attempt-1.*` 存在，
`round-1` 里 0 个 attempt，因为 R1 是机检短路）。这是一次真实的**假阳性检查**：
新门没有把正常的第二轮拦下来。

### 6.2 为什么没做到"真跑触发它"

每轮的 `diff.patch` 是 **相对上一轮分支**的（`base_ref = previous["branch"]`），
不是累计 diff。所以"两轮 diff 字节相同"意味着：从两个不同起点出发，
执行方产出了逐字节相同的补丁。R1 是"新建文件 +ok"，R2 只能表现为
"修改 -ok +ok2"这类相对改动，天然不同。

而且执行方**一点都没改**时更早就有防线：收集阶段 `NoChangesError("no changes detected")`
直接 BLOCKED，根本走不到验收。也就是说，真实运行里能触发"字节相同 diff"的
情形极窄。

按要求**没有为凑证据去改工件**：本批的真跑证据就是上面这条"未触发"的负结果，
P1-3 的正面证据来自 §5 的 `verify_round` 全路径夹具（模型 0 次）。

### 6.3 doctor（改后实测）

```text
$ date +%H:%M:%S ; uv run ag doctor ; date +%H:%M:%S
23:59:27
Codex (executor)                           PASS   7.39s
Codex (default verifier, read-only)        PASS  18.69s
Codex (tool execution, danger-full-access) PASS   8.72s
DSH (optional verifier)                    FAIL   5.03s   需 DEEPSEEK_API_KEY
Pi (optional verifier)                     FAIL  30.61s   需智谱 key（ZHIPU_API_KEY）
Double-blind tier: ★★
00:00:38
```

71s，档位 ★★，验收方探针 18.69s 一次通过（没有触发第二次）。跨了日界：
起 2026-10-31 23:59:27，止 2026-11-01 00:00:38——照实写，不做修饰。

## 7. 顺带发现（未做）

1. **判据的边界**（§6.2）：按定义实现的是"两轮补丁字节相同"，真跑里极窄。
   若维护者想要"这一轮实质上什么都没推进"，更贴的判据是比较累计树
   （`git diff <base0>..HEAD` 或 tree 哈希）。本批按定义实现，不改判据。
2. **正常轮次的 `subject` 只存在 `verdict.subject` 里**，state 的轮次对象没有
   顶层 `subject` 键（只有 no-progress 轮次写）。报告已能从 verdict 取，未追加。
3. **`ag verify` 只能验 round-1**，所以无净进展只能由 `ag run` 或测试触发；
   这不是本批范围。

## 8. 偏离

| 项 | 维护者要求 | 实际 | 理由 |
|---|---|---|---|
| clean 提示命令 | 三行写成一串 | 三条独立命令 | 原文 `icacls … /T /C rd /s /q …` 会被 cmd 当参数 |
| 提示触发条件 | "删除失败的 except 分支" | 仅在 stderr 含三类权限关键词时追加 | 其他失败（如主工作树）加这段话是误导 |
| `failure_reason` 落点 | state 的轮次对象 | 同时进 `VerificationOutcome` | 否则 orchestrator 只能靠字符串前缀猜，等于猜错因 |
| 报告附加细节 | "写在后面" | 紧随主因的第二行 | 放在结论段末尾会被"改了哪些文件"隔开 |

## 9. 完成标志（真实输出）

```text
$ uv run pytest -q
153 passed, 2 subtests passed in 78.31s

$ uv run ruff check
All checks passed!

$ uv run ruff format --check
42 files already formatted

$ git status --porcelain
(空)
```

工作区干净，未 push（`5a5beef`、`fe8de2d` 与本报告所在提交都在本地）。
