# STEP 7：格式修复与 P0-4 超时树杀

日期：2026-10-30
上游：STEP 6（P0-1 机检硬门槛，`0250082`）
commit：格式修复 `2e83b65`，P0-4 `69337dc`

## 1. 范围

本步只做两件事，均来自维护者对批次 B 的验收结论：

| 编号 | 内容 | 性质 |
|---|---|---|
| A | 补齐 `ruff format`，让 CI 的两条检查都过 | 阻塞项，5 分钟 |
| P0-4 | 让超时真的按墙钟生效（Windows 孙进程持管道） | 阻塞项，无人值守的前提 |

未做：P0-2、P0-3、P1-x。未改 `schemas/`。未 push。

## 2. 复现（先证实，再改）

维护者给的机制，本机实测：

```text
$ uv run python - <<'EOF'
outer.cmd 内容：@echo off
                "<python>" -c "import time; time.sleep(12)"
subprocess.run([outer], capture_output=True, text=True, timeout=2)
EOF

before  subprocess.run(timeout=2) -> 12.6s
after   run_bounded(timeout_s=2)  ->  2.3s exit=124
```

`subprocess.run` 内部超时后 kill 的只是直接子进程（`cmd.exe`）。
真正的 worker（`node.exe` / `python.exe`）是孙进程，仍持有 stdout/stderr
管道，于是内部的第二次 `communicate()` 等到管道 EOF 才返回 —— 等到孙进程
自己睡醒。所以"超时"表现为"等满全部时间"。

因为 `codex` 永远是 `.cmd → cmd.exe → node.js`，这个形状不是 doctor 独有：
`runner.run`、`verifier._run_command`、`collector.run_checks`、
`doctor._run_probe_once` 四处全部受影响。而 `max_total_minutes` 只在阶段
之间检查（`orchestrator.py:337/425/525`），阶段内部挂住就永远到不了那些
检查 —— 推论成立：`ag run` 可能永久挂起。

## 3. 改动文件

| 文件 | 改动 |
|---|---|
| `src/agentgate/proc.py` | **新增**。`run_bounded()` + `BoundedResult` + 树杀/收尾工具函数 |
| `src/agentgate/runner.py` | 执行方调用改用 `run_bounded`；超时从异常分支改为 `completed.timed_out` 分支；删掉不再使用的 `_text` |
| `src/agentgate/verifier.py` | `_run_command` 改用 `run_bounded`；`_run_attempt` 增加 `timed_out` 分支（对齐原有 `TimeoutExpired` 分支的行为） |
| `src/agentgate/collector.py` | `run_checks` 改用 `run_bounded(shell=True)`；删掉不再使用的 `_text` |
| `src/agentgate/doctor.py` | `_run_probe_once` 改用 `run_bounded`；超时判定改为 `completed.timed_out` |
| `tests/test_bounded_proc.py` | **新增** 4 条测试 |
| `tests/test_adapter_runner.py` | 两条既有测试的 patch 目标从 `runner.subprocess.run` 改为 `runner.run_bounded`（见 §6） |

`git diff --stat`（不含两个新文件）：

```text
 src/agentgate/collector.py   | 30 ++++--------------------------
 src/agentgate/doctor.py      | 30 ++++++++++--------------------
 src/agentgate/runner.py      | 44 +++++++++++++-----------------------------
 src/agentgate/verifier.py    | 24 ++++++++++++++++--------
 tests/test_adapter_runner.py | 16 ++++++++--------
 5 files changed, 53 insertions(+), 91 deletions(-)
```

## 4. 调用点前后对照

### 4.1 `proc.run_bounded` 的形状

```python
run_bounded(command, *, cwd, stdin_text=None, timeout_s, env=None,
            shell=False, kill_grace_s=5.0) -> BoundedResult
```

- `Popen(..., stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True, encoding="utf-8")`
- Windows：`creationflags=CREATE_NEW_PROCESS_GROUP`；POSIX：`start_new_session=True`
- `communicate(timeout=timeout_s)`；超时后：
  - Windows `taskkill /F /T /PID <pid>`；POSIX `os.killpg(getpgid(pid), SIGKILL)`
  - 再 `communicate(timeout=kill_grace_s)` 收残余输出；仍超时就放弃管道、
    返回已收集的部分，**不再等**
- 超时统一 `returncode=124` + `timed_out=True` + 部分 stdout/stderr

`BoundedResult` 是 `subprocess.CompletedProcess` 的子类，只多了
`timed_out` 与 `duration_s` 两个属性，所以既有把 `CompletedProcess` 传来传去
的代码不需要改类型。

### 4.2 四个调用点

| 位置 | 改前 | 改后 |
|---|---|---|
| `runner.py:49` | `subprocess.run(input=prompt, timeout=...)`，靠 `except TimeoutExpired` | `run_bounded(stdin_text=prompt, ...)`，靠 `completed.timed_out` |
| `verifier.py:73` | 同上一行，无超时处理（异常直接冒到 `_run_attempt`） | `run_bounded(...)`；`_run_attempt:329` 处理 `timed_out` |
| `collector.py:72` | `subprocess.run(shell=True, timeout=...)` | `run_bounded(shell=True, ...)`；`exit_code=124 if timed_out` |
| `doctor.py:115` | `subprocess.run(timeout=probe.timeout_s)` | `run_bounded(timeout_s=probe.timeout_s)`；`doctor.py:130` 判 `timed_out` |

超时语义保持不变，只把"什么时候返回"修对了：

- `runner`：仍返回 `ErrorClass.TIMEOUT`、`exit_code=124`（没有走
  `classify_error`，因为 `classify_error(124, "")` 会落成 `UNKNOWN`）。
- `verifier`：仍产出 `exit_code=124` 的 `AttemptResult`，`stderr` 仍补
  `"verifier timed out"`。
- `collector`：仍产出 `exit_code=124` 的 `CheckOutput`，于是机检仍判
  `mechanical` 失败（不是 `BLOCKED`）。
- `doctor`：仍产出 `error="timeout after Ns"`，档位判定逻辑未动。

### 4.3 未改的地方

`orchestrator.py` / `report.py` / `collector._run_git` 里的 `subprocess.run`
保持原样：跑的是 `git`，本身没有 `.cmd` 包装与孙进程，且都是毫秒级命令。
把它们也换成 `run_bounded` 只会增加噪音，不增加保障。

## 5. 测试

`tests/test_bounded_proc.py`（4 条全绿）：

```text
$ uv run pytest tests/test_bounded_proc.py -q
....                                                                     [100%]
4 passed in 6.34s
```

| 测试 | 断言 |
|---|---|
| `test_run_bounded_kills_grandchild_and_returns_promptly` | 12s 孙进程 + `timeout_s=2` → `returncode==124`、`timed_out is True`、**实际墙钟 < 6s** |
| `test_run_bounded_normal_path_returns_output` | `returncode==0`、`stdout=="got:payload"`（stdin 传递正确）、`stderr==""` |
| `test_run_bounded_timeout_keeps_partial_output` | 超时前打印的 `start` 仍在 `stdout` 里，且墙钟 < 6s |
| `test_run_bounded_shell_mode_reports_exit_code` | `shell=True` 的字符串命令 `exit 3` → `returncode==3` |

第一条与第三条都断言**实际耗时**，不是断言"抛了异常"，否则固定不住这个 bug。
在 POSIX 上第一条用 `sh` 脚本包一层 python 子进程制造同样的"孙进程持管道"
形状，所以在 CI 的 Linux runner 上也是有效的回归测试。

## 6. 与提示词的偏离

**一处，必须报备**：`tests/test_adapter_runner.py` 有两条既有测试通过
`monkeypatch.setattr(runner.subprocess, "run", ...)` 打桩。`runner` 改用
`run_bounded` 后模块里不再有 `subprocess` 这个名字，两条测试直接
`AttributeError`。处理方式是把打桩目标换成 `runner.run_bounded`，其余断言
原样保留，只有一处键名随新签名变化：

```diff
-    assert calls[0][1]["timeout"] == task.budget.max_minutes_per_round * 60
+    assert calls[0][1]["timeout_s"] == task.budget.max_minutes_per_round * 60
```

`"shell" not in calls[0][1]` 这条仍然有效且仍有意义：`run_bounded` 默认
`shell=False`，runner 也不传，等于继续钉住"执行方命令不经 shell"。

除此之外没有改任何既有断言，没有为了变绿放宽任何条件。

## 7. 真实输出

```text
$ uv run pytest -q
98 passed, 2 subtests passed in 74.93s (0:01:14)

$ uv run ruff check
All checks passed!

$ uv run ruff format --check
36 files already formatted
```

`ag doctor`（本步之前 8 分钟不返回，两次被杀）：

```text
$ time uv run ag doctor
exit=0 elapsed_s=75
```

| backend | role | result | time |
|---|---|---|---|
| Codex (executor) | required | PASS | 10.25s |
| Codex (default verifier, read-only) | required | PASS | 11.69s |
| Codex (tool execution, danger-full-access) | optional | PASS | 16.91s |
| DSH (optional verifier) | optional | FAIL — 需 DEEPSEEK_API_KEY | 4.95s |
| Pi (optional verifier) | optional | FAIL — 需智谱 key（ZHIPU_API_KEY） | 30.34s |

```text
Double-blind tier: ★★
```

这同时是本次修复最有力的外部证据：修前同一个命令跑不完，修后 75 秒返回
且三项 required/optional 探测全部 PASS，档位仍是 ★★。

## 8. 遗留

- **没有真跑一次 `ag run`**。P0-4 的验证靠单元测试（墙钟断言）+ doctor 端到端
  （它走的正是同一条 `run_bounded` 路径）。真跑一次需要消耗执行方与验收方
  各一次额度，留给下一步做自改任务时顺带完成。
- `codex.js` 的 node 直连入口（潜在去掉 `cmd.exe` 这一层）**跳过**，按提示词
  的"若牵动太多调用点就跳过"。树杀已经让超时按墙钟生效，去掉中间层是优化
  不是修复，且需要新的路径探测，属于脆弱改动。
- 沙箱内的检查命令（`collector.run_checks`）现在也树杀。维护者复核时补做了
  这个用例，实测两条路径都按墙钟返回：

  ```text
  shell=True  单层子进程  → 2.3s exit=124 ✔
  shell=False 直接子进程  → 2.3s exit=124 ✔
  ```

  结论：`taskkill /T` 能穿过 `cmd.exe` 打到子进程，`shell=True` 路径**已验证**。
  （本步当初只测了 `shell=False`，所以写的是"未验证"；现按实测更正。）
