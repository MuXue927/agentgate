# STEP 13：P1-4 状态写入原子化与单一写入者

上游：STEP 12（P1-1 `holdout_checks`，`afc5326`，已推，CI 绿）
commit：本批唯一提交 `refactor: state.json 原子写入与单一写入者（P1-4）`

## 0. 直接回答

| 问题 | 答案 |
|---|---|
| `grep -rn "write_state(" src/` 还剩几处 | **4 行命中，全在 `status.py`**：`:112` 定义、`:109` 在 `create_state` 里、`:160` 在 `update_state` 里、`:147` 是 `update_state` docstring 里提到函数名的那一行。**功能代码里 0 处直接调用** |
| 核心回归用例改造前的失败信息 | `E  KeyError: 'no_progress'`，`tests\test_status.py:93`，`1 failed in 3.16s`（见 §5） |
| orchestrator 的 delta 怎么定位轮次条目 | `orchestrator.py:278` `_save()`：先把非 `rounds` 的字段整体赋给磁盘文档，再按 `entry.get("round")` 建索引，对每条内存轮次 `target.update(entry)`——**按 `round` 号定位、按键合并，不做整段替换** |
| passed | **168 → 176**（+8 条，见 §6） |

## 1. 范围

- `status.py` 新增 `StateError` / `load_state` / `create_state` / `update_state`，`write_state` 改为原子替换。
- verifier 六处、orchestrator 两处、`report.py` 一处，全部改走统一入口。
- 不改任何判决逻辑、判据、门顺序；不改 `round-<N>/state.json`（那是 collector 的轮次工件）。
- 不加锁、不引入文件锁库；不加新命令、不加开关；不 push。

## 2. 为什么这不是理论问题

批次 G 的真实 bug：verifier 把 `no_progress` 写进 `state.json`，`ag run` 随后用它自己那份
内存快照覆盖，报告只能写 `R?`。当时是在编排器里"抄回字段"打的补丁——补丁治的是那一个
字段，病根是**两个写入者各持一份快照，各做一次全量覆盖**。这一批把写入收成一个入口，
下一个新字段就不会再被吃掉。

同时修掉一个一直存在的窗口：`write_text` 直接覆盖目标文件，进程在写入中途被杀会留下
截断的 JSON——而 `state.json` 正是 `ag resume` 唯一的恢复依据。

## 3. `status.py`：四个入口

| 函数 | 语义 | 谁用 |
|---|---|---|
| `write_state` `:112` | 同目录临时文件 + `fsync` + `os.replace`，`finally` 清理残留；docstring 明确"不要从功能代码直接调" | 只在下面三个里 |
| `create_state` `:98` | 写第一版；**文件已存在就抛 `StateError`** | `start_task`（`:726`） |
| `update_state` `:143` | 读最新盘上文档 → `mutate()` → 原子写；返回 `mutate()` 的返回值 | verifier 四处（经 `_update_run_state`）、orchestrator `_save`、`report.write_report`、`set_status` |
| `load_state` `:74` | 读 + 校验必填键，缺键/坏 JSON 抛 `StateError`（消息带路径） | `resume_task`（`:754`）、`verify_round`（`:867`，`require=("rounds",)`） |

临时文件用 `tempfile.mkstemp(dir=run_dir, ...)` 而不是 `NamedTemporaryFile` 或 `/tmp`：
`os.replace` 只在同一文件系统内是原子的，而 `/tmp` 在 Windows 上常常是另一个卷。

`read_state` 的语义也改了：文件不存在仍返回 `None`（"这个 run 没写过状态"），
但**坏 JSON 现在抛 `StateError`**——"没有状态"和"有状态但没人读得懂"导致的是完全不同的动作，
把它们混成一个 `None` 就是静默重建的开始。

## 4. 六处调用点前后对照

改造前（`afc5326` 时的行号）：每处都是 `state_path = run_dir / "state.json"` → `json.loads` →
改 → `state_path.write_text(json.dumps(...))`。行为上六个写入者互不知情，最后写的赢。

| 原位置 | 原角色 | 现在 |
|---|---|---|
| `verifier.py:322` `_round_head_commit` | 读（取本轮 head） | `read_state(run_dir)`，`:328` |
| `verifier.py:566` `_record_blocked_round` | 写 | `update_state` via `_update_run_state`，`:602` → 调用 `:626` |
| `verifier.py:626` `_previous_mechanical_failures` | 读（无净进展基线） | `read_state(run_dir)`，`:660` |
| `verifier.py:679` `_record_no_progress` | 写 + 返回比较明细 | `update_state`，`:687` → `:726`（明细照旧 `return detail`，与是否写盘无关） |
| `verifier.py:727` `_record_holdout_failure` | 写 | `update_state`，`:730` → `:760` |
| `verifier.py:769` `_record_round_in_state` | 写判决 | `update_state`，`:763` → `:795` |
| `orchestrator.py:278` `_save` | 全量覆盖 | `update_state` + 按键合并，`:278` / `:305` |
| `orchestrator.py:695` `start_task` | 首写 | `create_state`，`:726` |
| `report.py:549` `write_report` | 全量覆盖（把汇总写回） | `update_state(run_dir, lambda current: persist_change_summary(current, summary))`，`:553` |

三处配套改动，都是为了"读的是盘上的真值"：

1. `_finish`（`orchestrator.py:335`）：`_save` 之后 **`latest = read_state(run_dir) or state`**，
   再用 `latest` 生成报告并作为 `RunOutcome.state` 返回。以前报告渲染的是循环的内存快照，
   现在渲染的是刚合并过的那份文档。
2. `verify_round`（`verifier.py:866`）：run state 存在时必须可读（`load_state(require=("rounds",))`）。
   它决定"本轮 head"和"无净进展基线"，读不出来就会**把无净进展这道门静默关掉**——那是最坏的失败方向。
3. `_read_round_state`（`verifier.py:341`）：轮次 `state.json` 坏 JSON 时报 `Unreadable round state: <路径>`，
   不再让裸 `JSONDecodeError` 冒出来，也不重收集、不当空文档。

## 5. 核心回归用例：先红后绿

用例：`tests/test_status.py::test_verifier_write_then_orchestrator_save_does_not_lose_fields`。
它按真实交错顺序复刻 P1-3：verifier 直接把比较明细写盘 → `ag run` 用"写入之前"的快照调 `_save`。
`no_progress` 和 `subject` 都是编排器内存里**从来不存在的键**（`subject` 只在判决里出现过，
轮次条目上的 `subject` 只有 verifier 写），所以它们能不能留下，直接回答"合并是按字段还是按条目"。

改造前（旧 `_save` 全量覆盖）：

```text
$ uv run pytest -q tests/test_status.py
        entry = read_state(run_dir)["rounds"][0]
        assert entry["status"] == "failed"
>       assert entry["no_progress"] == detail
E       KeyError: 'no_progress'

tests\test_status.py:93: KeyError
=========================== short test summary info ===========================
FAILED tests/test_status.py::test_verifier_write_then_orchestrator_save_does_not_lose_fields
1 failed in 3.16s
```

改造后：

```text
$ uv run pytest -q tests/test_status.py::test_verifier_write_then_orchestrator_save_does_not_lose_fields
.                                                                        [100%]
1 passed in 0.75s
```

## 6. 其余用例（6 条要求 + 2 条补充）

| 用例 | 断言什么 |
|---|---|
| `test_write_state_is_atomic_on_crash` | `os.replace` 抛 `OSError` 时异常照旧传出，**旧文件逐字节不变** |
| `test_state_has_no_temp_file_after_write` | 写完目录里只有 `state.json`，不留 `.state-*.tmp` |
| `test_verifier_write_then_orchestrator_save_does_not_lose_fields` | §5 |
| `test_two_writers_both_persist` | 两次 `update_state` 改不同字段，最终文件里两条都在 |
| `test_corrupt_state_is_reported_not_repaired` | `load_state` 抛 `StateError` 且消息含路径；`_read_round_state` 抛 `VerificationError` 且含路径；两个坏文件**读后仍原样** |
| `test_update_state_returns_mutator_result` | 返回值透传（`1` 得到 `1`，`dict.update` 的 `None` 得到 `None`） |
| `test_create_state_refuses_to_replace`（补） | 二次首写抛 `StateError: ... already exists`，旧文档不变 |
| `test_save_of_one_round_keeps_another_rounds_fields`（补） | 第 2 轮条目能追加、第 1 轮条目的 verifier-only 字段（`subject`）不被第 2 轮的保存波及 |

两条补充的理由：`create_state` 是新增的写入口，"不覆盖"是它唯一的存在理由，没有用例等于没写；
`test_save_of_one_round_keeps_another_rounds_fields` 验证的是"按 `round` 号定位"这句实现说明。

## 7. 真跑：一条完整 DONE 路径

批次 H 的真跑只走到 `holdout-failed`，没碰过 `_finish` 的正常收尾。这一批的改动全在写入路径上，
所以补一条走到底的：`D:/Projects/ag-tasks/p14-state.task.yaml`（公开 check 一条，`max_rounds: 2`）。

```text
Round 1: PROTECTED: none declared
Round 1: PASS → DONE
机检: 1/1 通过
Verdict: PASS（验收官）
DONE + 1 轮 + 75.5s
```

- run 目录内容：`DONE  report.md  round-1  state.json  task.yaml` —— **没有 `.state-*.tmp` 残留**。
- `state.json` 顶层键 20 个（`repo/rounds/effective_max_rounds/status/terminal_reason/usage_*/cost_*/
  change_summary/tier/...`），轮次键含 `verdict / mechanical / protected / head_commit / base_commit`。
- `status: done`、轮次 `verified_pass`、`verdict.verdict: PASS`、`mechanical.passed: ["probe-exact"]`、
  `updated_at: 2026-11-01T08:31:51+00:00`（UTC，与本机 +08 的 16:31 对应）。
- `change_summary.source: git`，文件列表 `['state-notes.md', 'state-probe.txt']` —— 这条走的是
  `report.write_report` 里那个 `update_state`，说明汇总确实落盘了。
- 报告首行：`# p14-state  DONE  1.3min / 1 轮 / $0.0031 / 双盲 ★★`（`★★` 是运行当时 run 头部记录的档位；
  本机同一时刻 `ag doctor` 的档位是探测快照，两者不必相同）。
- 跑完 `ag clean p14-state`：worktree 与 `ag/p14-state-r1` 分支已删，`runs/p14-state/` 保留作证据。

## 8. 偏离与边界

1. **`_save` 的合并是"按 key、内存值胜"**，不是"按时间戳判新旧"。它保护的是**编排器从未见过的键**
   （P1-3 的 `no_progress`、holdout 的 `holdout`、本轮 `subject`）。若两个写入者写同一个键且
   内存那份更旧，合并仍会采用内存值——`_drive` 在 `:518-559` 一直把 verifier 的返回值抄进内存，
   所以现实路径上不存在这种"同键不同值"。要彻底消除得给文档加版本号或加锁，本批按边界不做。
2. **`set_status` 在 run state 不存在时只写 marker，不再凭空造一份 state**。旧代码 `or {}` 会写出一份
   只有 `status` 的文档，那份文档过不了 `load_state` 的必填键校验，却会让目录看起来像一次可恢复的 run。
   现在没有文档就只留 marker；文档存在则走 `update_state`。
3. **`load_state` 的 `require` 是参数**。`resume` 要 `repo/effective_max_rounds/rounds` 三个键，
   `ag verify` 只读轮次条目，所以只要求 `rounds`——按需收窄，而不是把一套校验硬套到两个读者身上。
4. `read_state` 的坏 JSON 从"抛 `JSONDecodeError`/返回 `None`"改为抛 `StateError`，并在 `cli` 的
   `run/resume/verify/report` 四处的异常元组里加上它，保证用户看到的是路径和原因，不是 traceback。

## 9. 状态

- 两条 ruff 绿（`check` + `format --check`，45 files formatted）。
- `pytest -q`：**176 passed, 2 subtests passed**（改造前 168）。
- 工作区干净，唯一 commit `refactor: state.json 原子写入与单一写入者（P1-4）`，**未 push**。
