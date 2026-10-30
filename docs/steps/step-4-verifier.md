# STEP 4 报告：验收方 + 单轮裁决

状态：**PASS（第 4 步验收通过，未进入第 5 步）**

## 做了什么

- diff 采集改为先 `git add -A`，再取 `git diff HEAD`。
- worktree 记录创建时基线 commit；执行方自行 commit 后仍可用
  `git diff <base-commit>` 收集完整改动。
- 空变更直接写 `BLOCKED`，理由为 `no changes detected`，不写 `EXECUTED`。
- 执行提示明确禁止 `git commit` / `git add`。
- run 目录保存 `task.yaml` 快照，支持后续 `ag verify <task-id>`。
- 新增 `ag verify <task-id>` 单轮验收：
  - 从既有工件重新生成 `verifier_input.md`；
  - 使用 Codex `-s read-only`；
  - 强制 `verdict.schema.json`；
  - 平衡括号提取 JSON；
  - schema/JSON 失败只重试一次；
  - 两次失败写 `BLOCKED`，不猜 verdict；
  - 验收前后比较 `git status --porcelain`。
- 单轮裁决：
  - PASS → `DONE`；
  - FAIL → 保留 `verdict.json`，CLI exit 0；
  - BLOCKED → `BLOCKED`，CLI exit 2。
- 没有缺陷回灌，没有第 2 轮，没有 push。

## 改了哪些文件

- `src/agentgate/workspace.py`
- `src/agentgate/dryrun.py`
- `src/agentgate/collector.py`
- `src/agentgate/execution.py`
- `src/agentgate/spec.py`
- `src/agentgate/verifier.py`
- `src/agentgate/cli.py`
- `tests/test_execution.py`
- `tests/test_verifier.py`
- `tests/test_cli.py`
- `tests/test_dryrun.py`
- `STEP-4-报告.md`

## 验收命令与真实输出

### 1. 必然 FAIL 的真实任务

第一个草案把 `findstr ... ok2` 直接放进执行提示，Codex 在执行前发现
goal/check 冲突并回答“应该用 ok 还是 ok2”，因此被正确判为 `BLOCKED`，
不是我们需要验收的 FAIL。

最终 FAIL 用例没有重试第一个任务，而是使用新 task id：

```yaml
goal: create hello-fail.txt containing exactly ok
check:
  run: type hello-fail.txt
  expect: {exit: 0, stdout_contains: ok2}
```

执行方只看得到 check 的命令，不知道 `stdout_contains: ok2`，因此生成了
`hello-fail.txt = ok`，形成真实的 check 失败。

真实 `ag verify` 输出：

```text
Verdict: FAIL
Check hello-must-be-ok2 failed: expected stdout to contain 'ok2' but actual stdout is 'ok'
HC001 [high] hello-fail.txt: File content 'ok' does not match expected 'ok2'
  reproduce: type hello-fail.txt
  expect: ok2
  actual: ok
verify_exit=0
```

`runs/ag-sandbox-fail-v2/round-1/verdict.json` 实际内容：

```json
{
  "verdict": "FAIL",
  "summary": "Check hello-must-be-ok2 failed: expected stdout to contain 'ok2' but actual stdout is 'ok'",
  "checks": [
    {
      "name": "hello-must-be-ok2",
      "status": "FAIL",
      "evidence": "stdout: 'ok'; expected: 'ok2'"
    }
  ],
  "defects": [
    {
      "id": "HC001",
      "severity": "high",
      "where": "hello-fail.txt",
      "why": "File content 'ok' does not match expected 'ok2'",
      "reproduce": "type hello-fail.txt",
      "expect": "ok2",
      "actual": "ok"
    }
  ]
}
```

FAIL 是正常业务结果，退出码为 0。FAIL run 目录已保留给第 5 步。

### 2. 正面 PASS 用例

使用第 3 步留下的 `ag-sandbox-hello-v2` 工件，最终代码真实输出：

```text
Verdict: PASS
hello.txt contains exactly 'ok' as expected
verify_exit=0
```

`runs/ag-sandbox-hello-v2/round-1/verdict.json` 实际内容：

```json
{
  "verdict": "PASS",
  "summary": "hello.txt contains exactly 'ok' as expected",
  "checks": [
    {
      "name": "hello-content",
      "status": "PASS",
      "evidence": "exit code 0, stdout: 'ok', stderr: ''"
    }
  ],
  "defects": []
}
```

`runs/ag-sandbox-hello-v2/DONE` 存在。

### 3. 验收方只读性

PASS 用例：

```text
STATUS_BEFORE:
 A hello.txt
STATUS_AFTER:
 A hello.txt
status_equal=True
```

FAIL 用例：

```text
STATUS_BEFORE:
A  hello-fail.txt
STATUS_AFTER:
A  hello-fail.txt
status_equal=True
```

`ag verify` 在内部再次比较两次 `git status --porcelain`；不一致会写
`BLOCKED: verifier modified the worktree`。

### 4. 空 diff 拦截

在临时 Git 仓库中注入“执行成功但什么都不改”的结果，走同一
`execute_round` 路径，真实输出：

```text
blocked=True
reason=no changes detected
EXECUTED_exists=False
verifier_input_exists=False
exit=0
```

“执行方自己 commit”也有测试覆盖：

```text
tests/test_execution.py::test_executor_commit_is_still_collected PASSED
```

实现使用创建 worktree 时的基线 commit 兜底，比依赖可能不存在的
`origin/master` 更稳定。

### 5. verdict 提取鲁棒性

真实测试输出：

```text
tests/test_execution.py::test_no_changes_blocks_without_executed_or_verifier_input PASSED
tests/test_execution.py::test_executor_commit_is_still_collected PASSED
tests/test_verifier.py::test_extracts_fenced_json_with_extra_text PASSED
tests/test_verifier.py::test_extraction_skips_schema_object_for_verdict PASSED
tests/test_verifier.py::test_two_invalid_outputs_block_without_verdict PASSED
tests/test_verifier.py::test_verifier_modification_invalidates_result PASSED

6 passed in 9.10s
```

两次无效 JSON 的停止位置：

1. 第二次验收调用返回；
2. JSON 提取或 schema 校验再次失败；
3. 在 Arbiter 之前写 `BLOCKED`；
4. 不写 `verdict.json`，不猜 PASS/FAIL。

### 6. 测试、lint 与 doctor

```text
57 passed, 2 subtests passed in 43.20s
All checks passed!
25 files already formatted
```

`uv run ag doctor` 关键输出：

```text
Codex (executor)                    | required | PASS | 6.80s
Codex (default verifier, read-only) | required | PASS | 8.03s
DSH (optional verifier)             | optional | FAIL | 5.80s
Pi (optional verifier)              | optional | FAIL | 16.27s

[DSH (optional verifier)] FAIL — 需 DEEPSEEK_API_KEY
[Pi (optional verifier)] FAIL — 需智谱 key（ZHIPU_API_KEY）

Double-blind tier: ★★
```

## verdict 提取失败两次时工具停在哪

工具停在验收方调用之后、Arbiter 之前。它：

- 保留两次 `verifier-attempt-*` 的 stdout/stderr/exit；
- 写 `runs/<id>/BLOCKED`；
- 不写 `verdict.json`；
- CLI 以 exit 2 退出。

## 保留的 run 与 worktree

FAIL 用例保留：

- `runs/ag-sandbox-fail-v2/`
- `runs/ag-sandbox-fail-v2/round-1/verdict.json`
- `D:/Projects/ag-sandbox/wt/ag-sandbox-fail-v2-r1`
- 分支 `ag/ag-sandbox-fail-v2-r1`

PASS 用例也仍保留 worktree，未自动清理：

- `D:/Projects/ag-sandbox/wt/ag-sandbox-hello-v2-r1`
- 分支 `ag/ag-sandbox-hello-v2-r1`

## 偏离说明

1. 验收调用增加 `--output-schema <verdict.schema.json>` 和
   `-o <last-message-file>`。原因是 `codex exec` 的 stdout 还包含工具事件，
   最终消息需要单独落盘。解析仍校验原 `verdict.schema.json`，并保留原始
   stdout/stderr。
2. 提示要求验收方不要调用工具、不要提问，因为 diff、文件清单和 check 原始
   输出已经完整提供。只读边界仍由 `-s read-only` 强制，且前后 git status 比对。
3. `verifier_input.md` 重新生成时复用第 3 步的渲染函数，不读取
   `executor.log`，也不读取 state 的 executor 段。

## 需要人代跑的联网操作

无。本轮没有新增依赖，没有 push。
