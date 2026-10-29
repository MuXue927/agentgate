# AgentGate v0.2 实现报告

状态：**完成（2026-10-30）**

## 结论

`ag` 已具备一条命令完成 Codex 执行、只读验收、FAIL defect 回灌、
最多 `max_rounds` 轮、终态标记、一页纸报告和 `resume` 的能力。
没有 push，也没有自动合并。

当前双盲档位为 **★★**：Codex 执行 + Codex `-s read-only` 非 DeepSeek 验收。

## Doctor

```text
Codex (executor)                    | required | PASS | 7.38s
Codex (default verifier, read-only) | required | PASS | 8.36s
DSH (optional verifier)             | optional | FAIL | 5.20s
Pi (optional verifier)              | optional | FAIL | 15.47s

[DSH (optional verifier)] FAIL — 需 DEEPSEEK_API_KEY
[Pi (optional verifier)] FAIL — 需智谱 key（ZHIPU_API_KEY）

Double-blind tier: ★★
```

## 文件与行数

以下计数在实现提交 `c82dae0` 后取得：

| 文件 | 行数 |
|---|---:|
| `src/agentgate/cli.py` | 265 |
| `src/agentgate/collector.py` | 379 |
| `src/agentgate/doctor.py` | 417 |
| `src/agentgate/dryrun.py` | 161 |
| `src/agentgate/execution.py` | 176 |
| `src/agentgate/orchestrator.py` | 713 |
| `src/agentgate/report.py` | 127 |
| `src/agentgate/runner.py` | 88 |
| `src/agentgate/spec.py` | 226 |
| `src/agentgate/status.py` | 56 |
| `src/agentgate/validation.py` | 94 |
| `src/agentgate/verifier.py` | 516 |
| `src/agentgate/workspace.py` | 254 |
| `schemas/task.schema.json` | 148 |
| `schemas/verdict.schema.json` | 139 |
| 四个 task 模板 | 各 19 |

源码、测试、适配器、schema 与模板文件总数：

```text
source_test_schema_template_files=39
```

## 端到端真实报告

```markdown
# ag-sandbox-two-round  DONE  2.0min / 2 轮 / $0.0060 / 双盲 ★★

## 结论
Verification passed; the final branch is ready for human review.
verification passed

## 每轮
- R1: FAIL（number-wrong）
- R2: PASS

## 改了哪些文件
number.txt | 1 +
 1 file changed, 1 insertion(+)

## 你需要做的
git diff 0a56a0494c916c3b77b63d25c18a85bda73c75af..ag/ag-sandbox-two-round-r2
git merge --no-ff ag/ag-sandbox-two-round-r2
```

成本实测：

```text
15 requests / 1,539,570 tokens / $0.00596569
```

## 端到端判据

| 项 | 结果 |
|---|---|
| 一条命令启动 | PASS |
| 中途不干预 | PASS |
| R1 FAIL → defect 回灌 → R2 PASS | PASS |
| `report.md` 一页纸 | PASS |
| 待合并分支 | `ag/ag-sandbox-two-round-r2` |
| `resume` | 真实 R1 head 不变，R2 PASS，`DONE` |
| `max_rounds` | 真实 `--rounds 1` 后 `FAILED` |
| 同 defect 早停 | 真实 R2 后 `FAILED`，指纹 `2436278c0b38` |
| doctor | ★★ |
| push / auto-merge | 未执行 |

## 测试

实现提交 `c82dae0` 后复跑：

```text
71 passed, 2 subtests passed in 56.10s
All checks passed!
29 files already formatted
```

## 与方案的偏离

1. 验收方调用增加了 `--output-schema` 与 `-o`，确保工具事件流与最终
   verdict 分离；verdict 仍由同一 schema 校验。
2. `FAIL` verdict 被收紧为至少包含一个 defect，否则无法安全回灌。
3. 未知模型保持完整独立 family，不把所有未知模型归并成同一族。
4. 详细执行日志、每次验收尝试与 prompt 均保存在 run 工件中，但不进入
   `verifier_input.md`。
5. `resume --force` 会清理同一 task 的 AgentGate worktree 并重建 run；
   这是显式请求的危险操作，不是自动降级。

除以上差异外，没有实现附录 A 的额度降级、通知、GUI 工具适配或插件系统。

## 最终交付

- 半自动到全自动闭环：`ag run task.yaml`
- 中断恢复：`ag resume <task-id>`
- 清理：`ag clean <task-id>` / `ag clean --all`
- 报告：`runs/<task-id>/report.md`
- 终态：`DONE` / `FAILED` / `BLOCKED`，三者互斥
- 主待合并分支：`ag/ag-sandbox-two-round-r2`

详细逐步证据见 `STEP-1-报告.md` 到 `STEP-5-报告.md`。
