# STEP 8：P0-3 受保护路径（fail closed）

日期：2026-10-30
上游：STEP 7（格式修复 + P0-4 超时树杀，`69337dc`）
commit：`51c2c5f`（代码与测试），文档随本提交

## 1. 范围

| 做 | 不做 |
|---|---|
| `task.yaml` 可选 `protected`，M/D/R 拦、A 放行 | P0-2（判决绑定 subject） |
| 检查排在机检之前，命中即终态 BLOCKED | P1-x |
| `report.md` / `--dry-run` 永远打印一行 `PROTECTED:` | 改 `verdict.schema.json` |
| 模板与 `task-yaml-guide` 同步 | Docker / hook / 外部工具 |

另外顺手更正了 STEP 7 §8 那条"`shell=True` 未验证"——维护者已实测，
现在写的是"已验证"并附实测数字。

## 2. 语义：为什么按 git 状态，而不是按内容

P0-1 的机检比对 `expect` 与真实输出。执行方把 `assert x == 3` 改成
`assert True`，`pytest` 照样退出 0，机检全绿。**"这个文件被改过"这件事，
机检结构上就看不出来**，只能由 git 状态来讲。

| 状态 | 含义 | 处置 |
|---|---|---|
| `A` | 新增文件 | **放行** |
| `M` | 修改既有文件 | 命中 → BLOCKED |
| `D` | 删除文件 | 命中 → BLOCKED |
| `R` | 重命名 | 看**被删掉的那一侧** |
| `T` / `U` | 类型变化 / 未合并 | 命中 → BLOCKED |

`A` 必须放行：自改任务本身就要新增测试文件，连新增都拦，P0-3 会把
"让 AgentGate 自己变好"这件事一起挡死。

`R` 只看被删侧：`git mv tests/a.py src/a.py` 是"把一个受保护文件搬走"，
必须拦；`git mv src/x.py tests/x.py` 等价于"在受保护目录里新增一个文件"，
按 `A` 处理。两个方向都拦会让"允许新增"这条自相矛盾。

glob 语义自己实现（`protected.py::_compile`）：`*` 不跨 `/`，`**` 跨。
不依赖 `PurePath.match` —— 它在不同 Python 版本对 `**` 的处理不一致，
而这是安全边界，不能建立在"应该差不多"上。

## 3. 落点与顺序（本批的核心）

`verify_round` 里的执行顺序：

```text
573    protected = _protected_gate(task, worktree, base_commit, change_reader)
577    if not protected.ok:  -> BLOCKED, return（attempts=()）
592    mechanical = evaluate_checks(stored_checks)
596    if not mechanical.ok: -> 合成机检 FAIL
```

回答"先后顺序在代码哪一行"：**`verifier.py:573` 的 `_protected_gate` 调用先于
`verifier.py:592` 的 `evaluate_checks`；命中时 `577` 直接 return，机械门那行根本
不会被执行。** `tests/test_protected.py::test_protected_check_runs_before_mechanical`
用"机检产物不存在"来固定这一点——不是断言调用顺序，而是断言机检**没有留下任何记录**。

`_drive` 没改判定逻辑，只多两件事：把 `protected` 同步进 `state.json` 的轮次条目、
打一行事件。判定仍然只有 `verify_round` 一处，`ag run` 与 `ag verify` 共用同一道门。

## 4. 改动文件

| 文件 | 改动 |
|---|---|
| `src/agentgate/protected.py` | **新增**。`FileChange` / `Violation` / `ProtectedResult`、`parse_name_status`、`evaluate_protected`、整路径 glob |
| `src/agentgate/spec.py` | `TaskSpec.protected`（默认空），`load_task` 读可选字段 |
| `schemas/task.schema.json` | 新增 `protected: array<string>`（`minItems 1`、`uniqueItems`），不进 `required` |
| `src/agentgate/collector.py` | 新增 `read_changes()`：`git -c core.quotepath=false diff --name-status -M <base>` |
| `src/agentgate/verifier.py` | `_protected_gate`、`_write_protected`、`_record_protected_block`；`VerificationOutcome.protected`；`_clear_previous_outcome` 连 `mechanical.json`/`protected.json` 一起清；`_record_round_in_state` 多记 `protected` |
| `src/agentgate/orchestrator.py` | 同步 `protected` 进轮次、打事件；BLOCKED 轮把轮次状态写成 `blocked`（原来留 `executed`，报告读作"已执行但尚未验收"） |
| `src/agentgate/cli.py` | `ag verify` 与 `ag run` 都打印 `PROTECTED:` 行 |
| `src/agentgate/report.py` | 结论段最上面一行 `PROTECTED:` |
| `src/agentgate/dryrun.py` | `--dry-run` 打印 `PROTECTED:` |
| `templates/fix-bug.task.yaml`、`templates/write-tests.task.yaml` | 加 `protected` 示例与注释 |
| `tests/test_protected.py` | **新增** 16 个用例 |

未声明 `protected` 时的行为：不设隐含默认，报告与干跑都打印
`PROTECTED: none declared`。**唯一的例外**：本功能之前生成的旧 run 里没有这个
字段，`report.md` 不编造一行——`_protected_fact` 返回 None。这条不影响新 run，
因为新 run 的每个轮次都会写 `protected`。

## 5. 测试

`tests/test_protected.py`：6 条必需 + 10 条配套。

| 用例 | 断言什么 |
|---|---|
| `test_protected_modification_blocks` | M 命中 → `verdict is None`、reason 含 `M tests/test_sample.py`、验收方零调用、`BLOCKED` 文件存在 |
| `test_protected_deletion_blocks` | D 命中 → BLOCKED |
| `test_protected_rename_blocks` | `git mv` 后 → BLOCKED，reason 指向**被删侧** `tests/test_sample.py` |
| `test_protected_new_file_allowed` | `git add tests/test_added.py` → 正常走到验收方、PASS、`checked == 2` |
| `test_protected_absent_prints_none_declared` | 未声明 → `summary_line() == "PROTECTED: none declared"`，且 `report.md`、`--dry-run` 都出现该行 |
| `test_protected_check_runs_before_mechanical` | 机检产物 exit=`0`（机检本会全绿）但树里改了测试 → BLOCKED，且 `mechanical.json` **不存在** |
| `test_parse_name_status_handles_rename_and_quotes` | `R100` + 带空格/中文的引号路径 |
| `test_glob_semantics`（7 组参数） | `*` 不跨 `/`、`**` 跨、精确路径 |
| `test_added_files_are_never_violations` | A 不产生违规 |
| `test_run_output_prints_protected_line` | `ag run` 输出里有 `PROTECTED:` 行 |

```text
$ uv run pytest tests/test_protected.py -q
................                                                         [100%]
16 passed in 11.42s
```

## 6. 真实运行证据（两次真跑，不是构造）

临时任务放在 `tasks/` 下，跑完已删除；模型是真调用（执行方 deepseek-v4-flash、
验收方 nemotron），双盲 ★★。

### 6.1 第一单：`tmp-protected-probe` —— 先正常跑通，再手工改测试

`ag run` 第一轮 PASS（执行方只新增了 `TMP_PROBE.txt`，`protected.json` 里
`checked: 1 / ok: true`）。然后在 worktree 里往 `tests/test_expectations.py`
追加一行注释，再 `ag verify --force`：

```text
$ uv run ag verify tmp-protected-probe --force
PROTECTED: 命中 M tests/test_expectations.py
BLOCKED: protected-path-modified: M tests/test_expectations.py (matches tests/**)
exit=2
```

验收方调用次数，改前 → 改后：

```text
=== ATTEMPTS BEFORE ===
10                     # 上一轮 PASS 留下的 2 次 attempt × 5 个文件
=== ATTEMPTS AFTER ===
0                      # 被清空且没有重建 = 一次都没调
```

同一目录的 `mechanical.json`、`verdict.json` 也一并消失（`_clear_previous_outcome`
现在连这两类工件一起清），说明门在机检之前就 return 了。

落盘状态：

```text
$ cat runs/tmp-protected-probe/BLOCKED
protected-path-modified: M tests/test_expectations.py (matches tests/**)

run status: blocked
round status: blocked
round protected: {"declared": ["tests/**", "tasks/**", "schemas/**"], "checked": 2,
                  "ok": false, "violations": [{"status": "M",
                  "path": "tests/test_expectations.py", "pattern": "tests/**"}]}
```

`report.md` 结论段（修掉旧 PASS 残留之后）：

```text
## 结论
PROTECTED: 命中 M tests/test_expectations.py
R1 状态为 blocked。
最终分支仅新增 TMP_PROBE.txt（+1）。
暂不合并；阻塞原因：protected-path-modified: M tests/test_expectations.py (matches tests/**)。
```

### 6.2 第二单：`tmp-protected-run` —— 让执行方自己去改测试

任务目标直接写"在 `tests/test_expectations.py` 末尾追加一行注释"。执行方照做了，
于是门在第一轮就拦住，**验收方一次都没被调用**：

```text
$ uv run ag run tasks/tmp-protected-run.task.yaml --allow-self
Round 1: PROTECTED: 命中 M tests/test_expectations.py
BLOCKED: protected-path-modified: M tests/test_expectations.py (matches tests/**)
PROTECTED: 命中 M tests/test_expectations.py
BLOCKED + 1 轮 + 36.7s
exit=2
```

调用计数与工件：

```text
$ ls runs/tmp-protected-run/round-1/
artifacts  changed_files.txt  diff.patch  executor.log  executor_prompt.txt
protected.json  state.json  verifier_input.md          # 没有 mechanical.json

$ ls runs/tmp-protected-run/round-1/ | grep -c verifier-attempt
0

$ tail -6 runs/tmp-protected-run/round-1/executor.log
+# probe by executor
tokens used
3,347

$ cat runs/tmp-protected-run/round-1/changed_files.txt
M  tests/test_expectations.py
```

执行方 1 次（`executor.log` 里能看到它加的那一行，tokens 3,347）、验收方 0 次、
机检 0 次。这一单就是"改松测试 → 机检本会全绿 → 被 protected 拦在验收之前"的
完整现场。

清理：两单的 worktree、branch、`runs/` 目录与 `tasks/tmp-*.yaml` 均已删除
（`ag clean --force` 输出 `removed worktree` / `deleted branch`），`tasks/` 回到
原来的 2 个文件。

## 7. 偏离与自查

1. **`_record_protected_block` 不只写状态，还清掉旧的 `verdict`/`mechanical`/
   `defect_fingerprints`，并写 `terminal_reason`。** 起因是第一单证据里看到
   `--force` 复验后报告同时出现"R1 PASS"和"BLOCKED"。这不是 P0-3 的逻辑错，
   但报告自相矛盾就谈不上"状态可读"，所以在本批内修掉。
2. **`orchestrator` 把 BLOCKED 轮次状态从 `executed` 改成 `blocked`。** 同上：
   第二单报告原本写"R1 已执行但尚未验收"，让人以为还能再验一次。这一行改动
   是在第二单跑完之后做的，**没有新的真跑证据**；它由现有 114 个测试兜住，
   不改变任何判定逻辑。
   → **待复验项**：下次出现任何 BLOCKED 真跑时，确认 `state.json` 里该轮
   `status` 是 `blocked` 而不是 `executed`。若又看到 `executed`，说明这行回退了。
3. **`_clear_previous_outcome` 现在会删 `mechanical.json`/`protected.json`。**
   因为写入顺序调整成"先清后写"，不删会留下上一次 `--force` 的旧结论。
4. **旧 run 的报告不出 `PROTECTED:` 行**（见 §4 末）。这是有意的：不编造。

## 8. 遗留

- **`git diff --name-status` 不含未跟踪文件。** 现在靠 `collect` 里的 `git add -A`
  把新文件变成 `A`。若有人在 `ag verify` 前手工造了未跟踪文件，它们不会出现在
  判定里——但未跟踪 = 新增 = 放行，所以不构成漏拦。
- **`C`（copy）状态按 `M` 处理**（拦）。`git diff -M` 默认不开 `-C`，正常不会出现；
  出现时按 fail closed 处理，宁可误拦。
- **`constraints` 与 `protected` 可能互相打架**：任务要求"修改某条旧测试"又写了
  `tests/**`，会直接 BLOCKED 且不消耗轮次。这是设计意图（便宜地失败），
  但写任务的人得知道——已写进 `task-yaml-guide` §1.9 的"别套太死"。

## 9. 完成标志核对（真实输出）

```text
$ uv run pytest -q
114 passed, 2 subtests passed in 48.25s

$ uv run ruff check
All checks passed!

$ uv run ruff format --check
38 files already formatted

$ uv run ag doctor        # exit=0, 37s
Codex (executor)                       PASS   8.23s
Codex (default verifier, read-only)    PASS   7.67s
Codex (tool execution, full-access)    PASS  12.30s
DSH (optional verifier)                FAIL   4.20s   需 DEEPSEEK_API_KEY
Pi  (optional verifier)                PASS   3.14s
Double-blind tier: ★★★
```

档位从 STEP 7 的 ★★ 变成 **★★★**：Pi 这一路这次探测通过（上一批 30.34s 后
失败）。`doctor.py` 本批未改动，不是我改出来的。

> **档位是探测快照，不是稳定状态。** Pi 走的是智谱免费档，相邻两次探测可以在
> 通过/失败之间切换：STEP 7 30.34s 失败 → 本批 3.14s 通过 → 维护者复核时又
> 失败（`需智谱 key`）。所以正确说法是"免费档在抖"，不是"环境变了"。
> 引用这个数字时必须带时间戳，**不能写成"当前档位 ★★★"**。

测试数：98 → **114 passed**（`tests/test_protected.py` 新增 16 个用例）。
工作区干净，未 push。
