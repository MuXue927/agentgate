# `tasks/` —— 只放可以公开的任务定义

## 一句话

**这个目录里的每一个 `*.task.yaml` 都会进入公开仓库。** 含敏感数据的任务不要放这里。

## 为什么需要这条规矩

`task.yaml` 会原文落进三个地方：

1. 仓库的 `tasks/`（若放在这里）
2. `runs/<task-id>/task.yaml`（**已被 gitignore**，不会推）
3. `runs/<task-id>/round-N/executor_prompt.txt`（同上，不会推）

所以真正的泄露面只有第 1 处：**任务定义本身**。而它恰恰最可能带敏感信息——`goal` 里常写着公司名、内部系统名、真实业务细节。

## 放哪里

| 内容 | 位置 | 是否入库 |
|---|---|---|
| 能公开的示例、模板、自用任务 | `tasks/` | ✅ 会推 |
| 含公司名 / 内部链接 / 客户信息 / 个人数据的任务 | **仓库外**，例如 `D:/Projects/ag-tasks/` | ❌ 不会推 |
| 万一想放仓库内 | `tasks.local/` 或 `*.local.task.yaml` | ❌ 已在 `.gitignore` 拦截 |

**推荐仓库外**，理由很实际：`tasks.local/` 只是被 `.gitignore` 挡住，仍有三种意外可能踩到——
`git add -f`、暂时改 `.gitignore` 调试、或者换了台机器忘了这条约定。放到仓库外，物理上就不可能被 `git add .` 收进去。

## 怎么用仓库外的任务

`ag` 只要求一个路径，任务放哪都行：

```bash
cd D:/Projects/agentgate
uv run ag run --dry-run D:/Projects/ag-tasks/acme-report.task.yaml
uv run ag run D:/Projects/ag-tasks/acme-report.task.yaml
```

`repo` 字段指向**被改的那个仓库**（例如 `D:/Projects/other-project`），不是 AgentGate 自己。

## 判断标准

> 如果这条 `task.yaml` 被贴到公开 issue 里，你会不会不舒服？

- 会 → 放仓库外
- 不会 → 放 `tasks/`

按这条判断，`tasks/agentgate-docs.task.yaml` 是合格的：它只描述一个开源项目自己的文档整理。

## 另外两道保险

1. **`runs/` 已 gitignore**：执行日志、diff、verdict 都留在本地，不会推。即使任务涉及内部系统，产物也不会上网。
2. **任务里不要写死凭证**：`task.yaml` 只该有路径与验收标准。需要 key 的步骤（`uv add`、登录）写进 `constraints` 让人代跑，不要写进 goal。
