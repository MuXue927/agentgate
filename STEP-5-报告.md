# STEP 5 报告：轮次循环 + 无人值守硬化

状态：**PASS（第 5 步与 v0.2 收尾验收通过）**

## 三个补丁

### 1. 状态标记互斥

所有标记统一由 `status.write_marker()` 写入；写任何新标记前删除
`EXECUTED` / `DONE` / `FAILED` / `BLOCKED`。根级 `state.json.status` 是状态真相。

真实结果：

```text
ag-sandbox-two-round=DONE
ag-sandbox-hello-v2=DONE
ag-limit-one=FAILED
ag-repeat-defect-v2=FAILED
ag-repeat-defect-v4=FAILED
ag-resume-demo=BLOCKED
```

第 4 步遗留的 `DONE + EXECUTED` 已迁移；当前每个 run 只有一个状态标记。

### 2. 数字复跑纪律

以下计数均在本轮实现提交 `c82dae0` 之后，用报告中的原命令复跑：

```text
71 passed, 2 subtests passed in 54.67s
All checks passed!
29 files already formatted
```

文件计数命令与结果：

```powershell
$files=Get-ChildItem -Recurse -File -LiteralPath 'src','tests','adapters','schemas','templates' |
  Where-Object { $_.FullName -notmatch '\\(__pycache__|\.pytest_cache)\\' }
Write-Output "source_test_schema_template_files=$($files.Count)"
```

```text
source_test_schema_template_files=39
```

### 3. 已 PASS 的 run 禁止重验

真实命令：

```powershell
uv run ag verify ag-sandbox-two-round
```

```text
Run already passed verification; use `ag verify --force` to rerun.
exit=1
```

模型调用数为 0；`--force` 路径由 `test_passed_run_requires_force_for_reverify` 覆盖。

## 轮次循环

- 第 N 轮使用 `wt/<task>-r<N>` 和 `ag/<task>-r<N>`。
- 下一轮从上一轮分支创建，继承已修复内容。
- 每轮执行后由 orchestrator 提交该轮工作树，形成独立可比较分支。
- `FAIL` 后只把上一轮 defects 原文回灌，不传 summary。
- 指纹由归一后的 `(where, why)` 计算。结构化 exit-code 缺陷会归一为
  `exit-code expected=0 actual=1`，普通文件缺陷保留归一化路径与全文 why。
- `--rounds N` 覆盖且持久化到 run state。
- 连续两轮同指纹、轮次上限、总耗时超限和执行/验收非 0 分别落到
  `FAILED` / `BLOCKED`。

## 验收命令与真实输出

### 1. 真实两轮任务

命令：

```powershell
uv run ag run D:\Projects\ag-sandbox-two-round.task.yaml
```

```text
Round 1: FAIL (1 defect) → continue
Round 2: PASS → DONE
DONE + 2 轮 + 118.6s
Branch: ag/ag-sandbox-two-round-r2
Merge: git merge --no-ff ag/ag-sandbox-two-round-r2
exit=0
```

`ocx usage` 前后：

```text
BEFORE requests=308 totalTokens=76004606 estimatedCostUsd=0.3408351968
AFTER  requests=323 totalTokens=77544176 estimatedCostUsd=0.3468008824
DELTA  requests=15 totalTokens=1539570 estimatedCostUsd=0.00596569
```

两个 worktree 均保留：

```text
D:/Projects/ag-sandbox/wt/ag-sandbox-two-round-r1
D:/Projects/ag-sandbox/wt/ag-sandbox-two-round-r2
```

### 2. 一页纸报告

`runs/ag-sandbox-two-round/report.md` 全文：

````markdown
# ag-sandbox-two-round  DONE  2.0min / 2 轮 / $0.0060 / 双盲 ★★

## 结论
R1 FAIL（number.txt：实际 2，期望 3）；R2 PASS。
最终分支仅新增 number.txt（+1）。
建议合并。

## 每轮
- R1: FAIL（number-wrong）
- R2: PASS

## 改了哪些文件
```text
A	number.txt	+1 / -0
```

## 你需要做的
git diff 0a56a0494c916c3b77b63d25c18a85bda73c75af..ag/ag-sandbox-two-round-r2
git merge --no-ff ag/ag-sandbox-two-round-r2
````

首行含任务 id / 终态 / 轮次 / 耗时 / 成本 / 双盲档位；结论段为 2 行。

### 3. 标记互斥

```text
ag-sandbox-two-round=DONE
```

同一目录没有 `EXECUTED`、`FAILED` 或 `BLOCKED`。

### 4. 轮次上限

命令：

```powershell
uv run ag run D:\Projects\ag-limit-one.task.yaml --rounds 1
```

```text
Round 1: FAIL (1 defect) → FAILED
FAILED + 1 轮 + 52.2s
exit=1
```

实际 `effective_max_rounds=1`，没有第 2 轮。

### 5. 同一 defect 连续两轮

真实命令：

```powershell
uv run ag run D:\Projects\ag-repeat-defect-v4.task.yaml
```

```text
Round 1: FAIL (1 defect) → continue
Round 2: FAIL (1 defect) → FAILED
FAILED + 2 轮 + 178.9s
exit=1
```

`max_rounds=3`，因此不是轮次耗尽；state 终止原因：

```text
same defect persisted across consecutive rounds:
fingerprint=2436278c0b38;
id=permanently-failing-check-fail;
where=retry-v4.txt verification;
why=The acceptance check permanently fails because cmd /c exit 1 returns exit code 1 instead of expected 0;
R1 actual={"exit": 1};
R2 actual=exit code 1
```

实际 cost：

```text
$0.0101762808 / 2,726,649 tokens / 9 requests
```

### 6. resume

真实中断点在 R1 已落盘为 `verified_fail`、事件已输出 `continue` 后：

```text
paused=kill after R1 state persisted, before R2
status=running rounds=1
R1_status=verified_fail
R1_head=f78fc30785fb3a599aa5004369c1de54e68c210c
```

随后执行：

```text
uv run ag resume ag-resume-pass
Round 2: PASS → DONE
DONE + 2 轮 + 154.7s
Branch: ag/ag-resume-pass-r2
exit=0
R1_HEAD_BEFORE_RESUME=f78fc30785fb3a599aa5004369c1de54e68c210c
R1_HEAD_AFTER_RESUME=f78fc30785fb3a599aa5004369c1de54e68c210c
rounds=2 status=done
```

R1 head 未变化，说明 resume 没重复执行 R1。Mock 计数也明确：

```text
executions_before_resume [1, 2]
executions_after_resume [1, 2, 2]
r1_execution_count 1
verification_calls [1, 2]
final_status done
```

其中第一次 R2 是模拟“启动后中断”，resume 再执行一次 R2；R1 始终只执行一次。

### 7. 回归、lint、doctor

```text
71 passed, 2 subtests passed in 54.67s
All checks passed!
29 files already formatted
```

`ag doctor`：

```text
Codex (executor)                    | required | PASS | 7.73s
Codex (default verifier, read-only) | required | PASS | 7.73s
DSH (optional verifier)             | optional | FAIL | 4.80s
Pi (optional verifier)              | optional | FAIL | 15.48s

[DSH (optional verifier)] FAIL — 需 DEEPSEEK_API_KEY
[Pi (optional verifier)] FAIL — 需智谱 key（ZHIPU_API_KEY）

Double-blind tier: ★★
```

## defect 回灌 prompt 全文

`runs/ag-resume-pass/round-2/executor_prompt.txt`：

```text
You are the implementation executor continuing a failed AgentGate task.
Task id: ag-resume-pass
Goal:
In the repository root, create final-number.txt containing the first prime number.
Do not modify any other file.

Constraints:
- Do not modify files other than final-number.txt.
- Do not push or merge.

Previous-round defects (authoritative):
### defect-1
where: final-number.txt
why: File contains '2' but acceptance check expects '3'
reproduce: type final-number.txt
expect: stdout should contain '3'
actual: stdout contains '2'

Previous-round diff path: D:\Projects\agentgate\runs\ag-resume-pass\round-1\diff.patch
Fix the defects above. Do not change anything else.
Do not run git commit or git add. Do not push or merge.
```

没有使用 verdict `summary` 作为回灌内容。

## 同一 defect 指纹

算法：

1. `where` 归一空白、大小写；verification 上下文归为
   `verification-check`，普通文件路径保留文本。
2. `why` 先做空白/大小写归一。
3. 若含 exit-code 语义，提取 expected/actual；缺失数字时从 defect 的
   `expect` / `actual` 提取。
4. 对 `(normalized_where, normalized_why)` 做 SHA-256，取前 12 位。
5. 不使用每轮可能变化的 defect `id`。

真实 v4 两轮指纹均为：

```text
2436278c0b38
```

## v0.2 六条判据

| 判据 | 结果 |
|---|---|
| 一条命令跑通 | PASS：`ag run task.yaml` 完成 R1 FAIL → R2 PASS |
| 中途不碰键盘 | PASS：真实两轮全自动；resume 真实续跑成功 |
| 一页纸 report.md | PASS：首行完整，结论 2 行 |
| 可合并分支 | PASS：`ag/ag-sandbox-two-round-r2`，报告给出 merge 命令 |
| doctor ★★ | PASS：当前真实档位 ★★ |
| 全程未 push | PASS：没有执行 push；本地 3 个实现提交，未创建远端提交 |

## 偏离与边界

- 为了让唯一失败任务稳定产生两轮，测试 task 的隐藏 `stdout_contains`
  比 goal 更具体；没有放宽 checks。
- `FAIL` 现在要求至少一个 defect，否则无法回灌，工具会 `BLOCKED`。
- 真实首次 resume 演示中 R2 验收模型两次给出非 JSON，按铁律 `BLOCKED`；
  随后独立 resume 用例正常 `DONE`，没有把失败那次改写成成功。
- 本轮保留多个沙箱实验 worktree，便于复核；主交付分支是
  `ag/ag-sandbox-two-round-r2`。

## 提交

```text
411c1f5 fix: 状态标记互斥与 verify 重跑保护（第 5 步）
352287c feat: 多轮执行、缺陷回灌与 resume（第 5 步）
c82dae0 feat: 一页纸报告、结构化指纹与可恢复终态（第 5 步）
```

未 push。
