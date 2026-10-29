# STEP 3 报告：执行方（Codex）+ 工件收集

状态：**PASS（第 3 步验收通过，未进入第 4 步）**

## 做了什么

- 模板 `repo` 改为 `D:/Projects/<your-repo>` 占位符。
- 默认拒绝把 AgentGate 自身作为目标；只有显式 `--allow-self` 才允许。
- 新增隔离子命令 `ag clean`：
  - `ag clean <task-id>` 只清该任务的 `ag/<task>-rN` worktree 与分支；
  - `ag clean --all` 只清目标仓库 `wt/` 下 worktree 和 `ag/*` 分支；
  - 默认拒绝脏 worktree；`--force` 才删除；
  - 重复执行输出“无残留”且 exit 0。
- 新增固定 Codex Adapter，含 `build_cmd` / `parse` / `RunResult` / `ErrorClass`。
- 新增 Runner：参数列表、无 shell、每轮超时、起进程前同族校验。
- 新增 Collector：check 原始输出、diff、文件清单、executor.log、state、验收输入。
- `ag run` 已接真实执行；成功写 `EXECUTED`，非 0 写 `BLOCKED` + 原始 stderr。
- 本轮没有调用验收方，没有合并，没有 push。

## 改了哪些文件

- `templates/add-feature.task.yaml`
- `templates/fix-bug.task.yaml`
- `templates/write-tests.task.yaml`
- `templates/update-docs.task.yaml`
- `src/agentgate/spec.py`
- `src/agentgate/workspace.py`
- `src/agentgate/dryrun.py`
- `src/agentgate/cli.py`
- `src/agentgate/adapters/__init__.py`
- `src/agentgate/adapters/codex.py`
- `src/agentgate/runner.py`
- `src/agentgate/collector.py`
- `src/agentgate/execution.py`
- `adapters/__init__.py`
- `adapters/codex.py`
- `tests/test_clean.py`
- `tests/test_adapter_runner.py`
- `tests/test_execution.py`
- `tests/test_cli.py`
- `tests/test_dryrun.py`
- `tests/test_spec.py`
- `tests/fixtures/self-repo.task.yaml`
- `STEP-3-报告.md`

## 验收命令与真实输出

### 0. 小项：clean 幂等与自指向保护

创建 3 个内部 worktree 后第一次 `ag clean --all`：

```text
removed worktree: D:\Projects\agentgate\wt\step3-final-clean-a-r1
removed worktree: D:\Projects\agentgate\wt\step3-final-clean-b-r1
removed worktree: D:\Projects\agentgate\wt\step3-final-clean-c-r1
deleted branch: ag/step3-final-clean-a-r1
deleted branch: ag/step3-final-clean-b-r1
deleted branch: ag/step3-final-clean-c-r1
exit=0
worktree D:/Projects/agentgate
HEAD 2085e6080d1ba424333a7531d5e06ae77505b635
branch refs/heads/master

branches=empty
```

第二次：

```text
无残留
exit=0
```

模板现在保留占位符，直接 dry-run 会拒绝不存在的目标：

```text
Target repo does not exist: D:\Projects\<your-repo>
exit=1
```

把 `repo` 指向 AgentGate 自身：

```text
task.yaml repo points to AgentGate itself. Choose the target repository or pass --allow-self explicitly.
self_exit=1
```

`--allow-self` 已由自动化测试覆盖：只有显式传参才创建自指向 worktree，测试末尾清理。

### 1. 真实最小任务

目标仓库：`D:/Projects/ag-sandbox`。

```text
Round 1 executed → runs\ag-sandbox-hello-v2\round-1
run_exit=0
```

`state.json` 的实际情况：

```json
{
  "task_id": "ag-sandbox-hello-v2",
  "round": 1,
  "status": "executed",
  "worktree": "D:\\Projects\\ag-sandbox\\wt\\ag-sandbox-hello-v2-r1",
  "branch": "ag/ag-sandbox-hello-v2-r1",
  "executor": {
    "harness": "codex",
    "model": "deepseek-v4-flash",
    "exit": 0,
    "duration_s": 31.093,
    "error_class": "OK"
  },
  "model_pair": {
    "tier": "★★",
    "executor_family": "deepseek",
    "verifier_family": "nemotron"
  }
}
```

### 2. 工件齐全

```text
D:\Projects\agentgate\runs\ag-sandbox-hello-v2\round-1\diff.patch
D:\Projects\agentgate\runs\ag-sandbox-hello-v2\round-1\executor.log
D:\Projects\agentgate\runs\ag-sandbox-hello-v2\round-1\state.json
D:\Projects\agentgate\runs\ag-sandbox-hello-v2\round-1\verifier_input.md
D:\Projects\agentgate\runs\ag-sandbox-hello-v2\round-1\artifacts\01-hello-content.exit.txt
D:\Projects\agentgate\runs\ag-sandbox-hello-v2\round-1\artifacts\01-hello-content.stderr.txt
D:\Projects\agentgate\runs\ag-sandbox-hello-v2\round-1\artifacts\01-hello-content.stdout.txt
```

`runs\ag-sandbox-hello-v2\EXECUTED` 也存在。

### 3. diff 内容正确

```diff
diff --git a/hello.txt b/hello.txt
new file mode 100644
index 0000000..9766475
--- /dev/null
+++ b/hello.txt
@@ -0,0 +1 @@
+ok

--- git status --porcelain ---
?? hello.txt
```

### 4. 验收输入不含执行方自述

禁止词计数：

```text
我=0
已完成=0
I have=0
Done=0
```

`executor.log` 中确实保存了执行方的自然语言输出，例如
`Created hello.txt ...`，但它没有进入 `verifier_input.md`。

### 5. 同族模型在执行前被拒

```text
Executor and verifier are in the same model family ('deepseek')；同一模型族禁止自证。Execution='deepseek-v4-pro', verification='deepseek-flash'.
exit=1
worktree_list_unchanged=True
run_dir=absent
```

### 6. 测试与 lint

```text
.........................................                              [100%]
41 passed, 2 subtests passed in 22.24s
```

```text
All checks passed!
23 files already formatted
```

## verifier_input.md 内容逐项对应

1. **goal 与 checks**：文件中的 `## Goal` 和 `## Acceptance checks`，
   内容来自 `task.yaml`。
2. **diff 全文**：文件中的 `## Diff`，内容来自 `diff.patch`，包括新增
   `hello.txt` 的全文和 `git status --porcelain`。
3. **check 原始输出**：文件中的 `## Check evidence`，逐条包含 check 命令、
   exit code、原始 stdout 与 stderr。
4. **文件清单**：文件中的 `## Changed file list`，内容来自
   `git status --porcelain`。

文件中没有 `executor.log`、执行方 stdout、executor model、执行摘要或任何
“我做了什么”的自然语言陈述。

## 实际 token / 费用

最终真实运行的 `ocx usage --range today --surface codex --json`：

```text
BEFORE requests=94 totalTokens=15828028 estimatedCostUsd=0.0934074512
AFTER  requests=107 totalTokens=18170813 estimatedCostUsd=0.1041227768
DELTA  requests=13 totalTokens=2342785 estimatedCostUsd=0.01071533
```

Codex 自身 `executor.log` 同时报告 `tokens used 4,494`。两者口径不同：
`ocx` 报告包含缓存上下文，故以任务要求的前后差值为准。

## 最小任务 worktree 是否保留

**保留。**

```text
worktree D:/Projects/ag-sandbox/wt/ag-sandbox-hello-v2-r1
branch refs/heads/ag/ag-sandbox-hello-v2-r1
```

该 worktree 和分支未清理，留给第 4 步验收。

## wt 清理是否幂等

是。连续两次 `ag clean --all` 都 exit 0，第二次输出“无残留”。

## 偏离说明

- 模板改成占位符后，不能不经编辑直接对真实仓库 dry-run；这是本次要求的安全改动。
- `ag clean` 只删除已注册在目标仓库 `wt/` 下的 AgentGate worktree；未注册的普通
  `wt/` 目录不会被猜测删除。

## 需要人代跑的联网操作

无。本轮没有新增依赖，依赖安装与 lockfile 未改动。
