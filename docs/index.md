# AgentGate 文档索引

AgentGate 是一个单用途 CLI，命令名为 `ag`。人编写一次 `task.yaml`，Codex
在隔离 worktree 中执行任务，另一个不同模型的验收方读取工件并给出结构化
`verdict`；验收不通过时，工具只把缺陷原文回灌给执行方，最多运行
`max_rounds` 轮，最后留下状态标记、一页纸报告和待人工合并的本地分支。

项目目标不是建设通用智能体平台，而是把执行与验收中的“必须有人在场”降到
最低：人只负责写清任务规格和合并前做一次最终判断。v0.2 的固定边界包括：
不做自动降级链、key 治理、通知、GUI 工具适配、插件系统、ORM、工作流引擎、
Web UI；不 push，也不自动合并。

## 推荐阅读顺序

1. 先读 [验收记录](acceptance.md)，用一页确认 v0.2 的六条交付判据、真实
   终端证据和当前双盲档位。
2. **开始写第一个任务前，先读 [如何写 task.yaml](task-yaml-guide.md)**：
   九个字段、checks 的写法准则、五类任务模板、反模式与失败诊断。
3. 再读 [设计方案](design.md)，理解工件驱动、反自证、轮次循环、状态机和
   无人值守边界的完整设计。
4. 然后读 [v0.2 实现报告](AgentGate-v0.2-实现报告.md)，查看最终实现范围、
   端到端结果、测试数据以及与设计方案的偏离。
5. 需要复核实现过程时，按顺序阅读五份 STEP 报告。每份都记录当步范围、
   改动文件、验收命令、真实输出和已知边界。

## 稳定入口

常用命令：

```powershell
uv run ag doctor
uv run ag run D:\Projects\my-task.yaml
uv run ag resume <task-id>
uv run ag report <task-id>
```

运行状态位于 `runs/<task-id>/`，终态标记为互斥的 `DONE`、`FAILED` 或
`BLOCKED`。真正完成的任务会提供 `report.md` 和 `ag/<task>-r<N>` 分支；
合并仍需人工审阅后执行。对于中断的运行，先使用 `ag resume`，不要猜测失败
原因或自动更换后端。

## 全部文档

- [本页：文档索引](index.md)
- [如何写 task.yaml（详细方法与准则）](task-yaml-guide.md)
- [同类项目调研与可借鉴清单](landscape.md)
- [v0.2 验收记录](acceptance.md)
- [v0.2 设计方案](design.md)
- [v0.2 实现报告](AgentGate-v0.2-实现报告.md)
- [STEP 1：项目骨架与 ag doctor](steps/step-1-doctor.md)
- [STEP 2：规格、schema、worktree 与 dry-run](steps/step-2-schema.md)
- [STEP 3：Codex 执行方与工件收集](steps/step-3-executor.md)
- [STEP 4：只读验收方与单轮裁决](steps/step-4-verifier.md)
- [STEP 5：轮次循环与无人值守硬化](steps/step-5-loop.md)
- [STEP 6：机检硬门槛（P0-1）](steps/step-6-expect-gate.md)
- [STEP 7：超时树杀（P0-4）](steps/step-7-timeout.md)
- [STEP 8：受保护路径（P0-3）](steps/step-8-protected.md)
- [STEP 9：证据包与约束判定（P1-2）](steps/step-9-evidence-pack.md)
- [STEP 10：判决绑定证据身份（P0-2）](steps/step-10-subject.md)

所有相对链接均以 `docs/` 为基准。STEP 文件由根目录迁入 `docs/steps/`，
正文内容保持不变；`docs/design.md` 仍是规范设计参考，不因整理文档而删改
章节。若文档和实际行为不一致，应先停止任务、保留原始输出，再按设计方案与
维护者本地的项目约定核对，而不是静默选择一种解释。
