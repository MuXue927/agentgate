# STEP 15：测试用假后端与三条端到端回归（P1-5）

上游：STEP 14（P2-2 原因码，同批）
commit：`test: 假后端与三条端到端回归`

## 0. 直接回答

| 测试 | 耗时 |
|---|---|
| `test_fake_loop_reflows_fail_then_pass` | **2.73s** |
| `test_fake_loop_stops_on_no_progress` | **2.62s** |
| `test_fake_loop_stops_on_protected_path` | **1.60s** |

（三条合计 7.80s，不含 setup；不触网、不调模型。全量从 86.7s 涨到 94.1s，涨幅正好是这三条。）

## 1. 为什么不用现成的 `FakeRoundTools`

`tests/test_orchestrator.py` 里已有 `FakeRoundTools`，它把 **executor 和 verifier
一起换掉**。那种假后端能测编排器的分支，但测不到"门真的拦住了"——比如无净进展，
判定发生在 `verify_round` 内部，verifier 一旦被整个替换，这道门就永远不执行，
测试只是证明了假后端会返回假判决。

所以 `tests/fakes.py` 把边界画得更窄：

| 被替换 | 用什么替 | 仍然是真代码 |
|---|---|---|
| 执行方进程 `agentgate.runner.run` | 一次 `rounds[n](worktree)` 文件写入 | worktree 创建、`git diff` 采集、机检命令、工件落盘、checkpoint 提交 |
| 验收方进程（`command_runner`） | `verdicts[n]` 构造好的 JSON | protected 门、机检门、holdout、无净进展、证据包、subject 冻结与回抄、状态写入、报告 |

一句话：**假的只有模型进程，门和账都是真的。**

## 2. 两个脚本后端

```python
executor = scripted_executor({1: write_state_file("draft"), 2: write_state_file("done")})
verifier = scripted_verifier({2: pass_verdict("state-ok")})
```

- `scripted_executor(rounds)`：调真 `execute_round`，只在它内部 `patch`
  `agentgate.runner.run`。返回对象带 `.calls`（执行过哪几轮）。
- `scripted_verifier(verdicts)`：调真 `verify_round`，把 `command_runner` 换成
  "查表返回 JSON"。`subject` 在**调用时**从 `round-N/subject.json` 读出来回抄——
  它只在那时才存在。
- 表里没有的轮次 = 这一轮必须被门拦住；如果模型还是被调到了，runner 直接
  `AssertionError`。测试不可能靠"没配的判决"混过去。
- `.calls`（跑了哪几轮）与 `.answered`（模型真正回答了几次）分开：保护路径那一轮
  `verify_round` 会跑，但模型在门前就被挡住了，`answered == []` 才是"没问模型"的证据。

## 3. 三条回归

| 测试 | 断言 |
|---|---|
| `reflows_fail_then_pass` | `executor.calls == [1, 2]`、`verifier.answered == [2]`（R1 由机检判决，模型没被问）、终态 `done`、`DONE` 标记、事件序列 `机检未通过 → FAIL (1 defect) → continue → PASS → DONE`、报告 `- R1: FAIL（M1）` / `- R2: PASS` |
| `stops_on_no_progress` | 两轮同一 check 失败 → `failed` + `no-progress`；`no_progress` 明细（前一轮、失败集合）落盘；`verifier.answered == []`；报告"无净进展"与 `- R2: no-progress` |
| `stops_on_protected_path` | 执行方改 `tests/test_keep.py` → `blocked` + `protected-path`；run 级 `terminal_code`、轮次 `blocked_code`、`BLOCKED` 标记、报告 `BLOCKED [protected-path]` 四处一致；执行方只被调用 1 次（没有第二轮） |

机检命令用 `findstr /x /c:"done" state.txt` 这类 cmd 内建，不用 `python -c`：
每条测试要跑 2–4 次机检，起两次解释器就吃掉 0.5s，而这些测试的价值在于"门真的在"，
不在于测 Python 启动速度。

## 4. 边界

- 只在 `tests/`：`src/` 不导入，CLI 没有开关，不是运行时降级路径（铁律 1 不受影响）。
- 假后端不模拟"模型写坏了输出"——那是 `verify_round` 重试用例的活，已有覆盖。
- 耗时主要在两轮 worktree + 两次 `git commit`，属真实开销；再加轮次会线性增长。

## 5. 状态

- 全量：**182 → 185 passed**（阶段 A 后 182，阶段 B +3）。
- 两条 ruff 绿，`ruff format --check` 49 files 不变。
- 未 push。
