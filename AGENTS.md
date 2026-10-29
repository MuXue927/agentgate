# AgentGate 实现约定

## 项目是什么
单一用途 CLI `ag`：人写一次 task.yaml，Codex 执行、独立只读验收、
不过就回灌重做最多 3 轮，交一页纸报告 + 待合并分支。
目标只有一个：让执行和验收都不需要人在场。

## 规格来源
`D:\ProgramData\WorkBuddy\docs\AgentGate-跨Harness执行验收编排-设计方案.md`
重点 §0 / §0.6 / §1.2 / §2 / §3 / §4 / §7 / §9.1 / §10 / §12。**§6 是附录，跳过。**

## 铁律
1. 范围冻结：不做降级链、不做 key 治理、不做通知、不做 GUI 工具适配、
   不做插件系统、不做 ORM/工作流引擎、不做 Web UI。
2. 一会话只做一步，做完停下等人验。
3. 验收方不许改文件；验收后校验 git status 为空，否则验收无效。
4. 执行与验收禁止使用同一个模型。不同模型 + `-s read-only` = 合格；
   同模型 = 自证，禁止。
5. 验收方输入绝不含执行方的自然语言总结。
6. 不猜错因：非 0 退出 → BLOCKED + 原始 stderr，不自动重试/换模型/换后端。
7. 不 push、不自动合并。
8. 每条验收贴真实终端输出，不写"已验证"。

## 环境（实测，别再探索）
- 执行：`codex exec --skip-git-repo-check -C <dir> -m deepseek-v4-flash "..."`
- 验收默认：`codex exec -s read-only -m nvidia/nemotron-3.5-lightning:free`
- 验收可选（需配 key）：DSH headless / `pi -p --mode json --tools read,grep,find,ls`
- DSH GUI 内保存的 key 在 headless 下读不到；可选探测需要 `DEEPSEEK_API_KEY`。
- 不可用：Claude Code（端点失效）、ZCode（asar 未打通）、
  CodeArts / WorkBuddy（GUI 应用，有额度也不等于能无人值守）
- 不要试图探测 CodeArts / WorkBuddy / ZCode，浪费时间。

## 沙箱约束
worktree 建在 `D:/Projects/agentgate/wt/<task>-r<N>`，不要 `../`。`wt/`、`runs/` 进 .gitignore。

## 沙箱无网络
执行 Agent 的沙箱没有网络出口。任何新依赖都不要自行 `uv add` / `uv sync`；
在 STEP 报告里写“需要人代跑：`uv add <pkg>`”，并继续完成不依赖它的部分。

## 每步交付
`STEP-<N>-报告.md`：做了什么 / 改了哪些文件 / 验收命令与真实输出 / 有无偏离方案。
