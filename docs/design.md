# AgentGate：跨 Harness「执行 ↔ 验收」编排器 · 设计方案

> 版本 **v0.2**（2026-10-29 修订：按「省在场」而非「省转述」重定范围）
> 目标：让**执行与验收都不需要人在场**——人只写一次任务规格，等一页纸结论。
> 不追求"支持所有 harness"，只追求"我不用盯着"。

---

## 0. 结论先行

| 项 | 结论 |
|---|---|
| 技术上可行吗 | **可行**。本机至少 3 个 harness 有干净的非交互入口（Codex `exec`、Pi `-p --mode json`、DSH `headless`），且都能拿到"退出码 + 工件 + 会话落盘"三件套 |
| 核心原理 | **不靠转述，靠工件**。验收方读 `git diff` + 命令原始输出 + 退出码 + 结构化验收清单，**不读执行方的自述报告** |
| **本次范围（v0.2 砍定）** | **6–8.5 小时**：单对 harness（Codex 执行 / Pi 验收）+ 轮次循环 + 一页纸报告 + 可恢复。**不做**跨 harness 降级、成本治理、桌面通知、7 个适配器 |
| 为什么砍 | 你的痛点是"**我必须在场**"，不是"转述要 3 分钟"。跨 harness 降级一个月用不到两次，手动切一下就行；为它多花 20 小时是拿注意力换注意力 |
| 交付判据 | 一条命令启动 → 中途不碰键盘 → 得到 `verdict: PASS` + 一个待合并分支 + 一页纸 `report.md` |
| 最大风险 | **造工具本身是最大的注意力支出**。6–8.5h 若按 1.5h/天摊，是本周末 + 下周末；但若中途反复返工，就会变成"为了复习国考而造工具，结果更没复习" |

---

## 0.5 前提变了：这次该做的理由，跟我上次反对的理由不同

我在 v0.1 里明确反对过，理由是"你每天只花 2–3 分钟转述，为它造 30 小时工具不划算"（v0.1 的原始反对意见已被 v0.2 的 §11 风险表取代，其结论仍被保留在下面的"注意力账"里）。

**那个判断基于一个错误的前提**：我当时把成本算成"转述时间"。真实成本是：

| 我上次算的 | 实际发生的 |
|---|---|
| 转述 + 验收 ≈ 2–3 分钟/次 | **每次任务都要你在场**：看执行摘要 → 判断对不对 → 决定下一步 → 转述 → 再判断验收意见 |
| 一次任务 ≈ 5 分钟注意力 | **一次任务把注意力切成 4–6 段**，每段都要重新进上下文（这是真正的成本，不是分钟数） |
| 一天一个任务 | 一天 2–4 个来回（M0 期间实际是 c8→c26，18 轮） |

**代价的实证**：这十几天你复习国考的时间是 0。所以 v0.2 的目标从"省转述"改成**"省在场"**——工具的价值不在于让你少打几个字，而在于**让你可以离开房间**。

这个目标下的设计取舍完全不同：

| 目标 = 省转述 | 目标 = 省在场 |
|---|---|
| 要支持多 harness（转述来源多） | 只要 2 个够用（可能一辈子只用一个组合） |
| 要有成本治理（怕花超） | 只要硬预算上限（超了就停，别管花多少） |
| 要有桌面通知（转述完要提醒你） | **要"不提醒"**：跑完写文件，你有空再看 |
| 要漂亮的状态机 | 要**能 resume**：机器休眠、网络断、代理掉，回来能续 |

---

## 0.6 它不解决什么（先说清边界，免得期望错位）

| 它不做 | 后果 |
|---|---|
| **不替你决定"这个任务值不值得做"** | 任务规格写错，它会在错的路上多跑几轮，把错误放大。规格是你唯一不能外包的一步 |
| **不保证验收方一定对** | 验收方会漏（尤其大 diff）。所以合并前你仍要点一次头（`before_merge` 永不自动） |
| **不消除"停下来问你"** | 遇到 UNKNOWN 错误、验收连续两轮同一条 defect、预算超限，它会停下等你。这类事件一天可能有 0–2 次，比现在少一个数量级，但不是零 |
| **不替你写 task.yaml** | 这是你剩下的唯一固定成本（约 10–15 分钟/任务）。§10.1 给了 4 个模板把这个压到 3–5 分钟 |
| **不改善 agent 本身的水平** | 执行方写不好，验收方拦得住，但拦住的结论还是"重写"。它提升的是**利用率的确定性**，不是**上限** |

---

## 1. 本机现状盘点（2026-10-29 实测，不是推测）

| Harness | 非交互入口 | 沙箱/权限 | **2026-10-29 实测可用性** | 会话落盘 / 观测 |
|---|---|---|---|---|
| **DSH** 0.2.1-rc.3 | `dsh headless "<task>" --json`（`-` 读 stdin） | ✅ fs + bash 沙箱 | ✅ **可用**（本会话宿主，已知能跑） | `~/.dsh/`，`--session-id` 续跑 |
| **Codex** 0.162.1 | `codex exec`（`-C` `-s` `--ephemeral`） | ✅ 内核沙箱（`workspace-write`） | ✅ **可用**：exit 0 回 `OK`，单次 5,149 tokens；模型 `deepseek-v4-flash` 经 ocx | `~/.codex/sessions/**/rollout-*.jsonl` |
| **Pi** 1.4.0 | `pi -p` + `--mode text\|json\|rpc`；`--model provider/model`；`--tools read,grep,find,ls`；`--no-session` | ❌ **无沙箱、无权限确认** | ⚠️ **装了但缺凭证**：`~/.pi/agent/auth.json` 仅 2 字节（空），无 `ZHIPU_API_KEY`/`OPENROUTER_API_KEY` 用户级环境变量 | `D:\ProgramData\Pi\*.jsonl` |
| **Claude Code** 2.1.311 | `claude -p --output-format json` | ✅ | ❌ **不可用**：`claude -p` 卡死 240s；`anyrouter.top` 返回 **000×2**（代理已开仍不通，端点本身失效） | `~/.claude/projects/**` |
| **CodeArts Agent** 26.10.100 | `codearts-agent chat "<prompt>"` —— **实为打开 IDE 对话面板**（选项含 `--maximize` / `--reuse-window` / `--new-window`） | ❌ **GUI，无 headless** | `--status` 明确要求"已有运行实例"；安装目录是 VS Code 系 Electron | 未找到可借用的 API 端点 |
| **ZCode**（Electron 222 MB） | ❌ 未打通 | — | ❌ asar 内 `zcode.cjs` 在 `ELECTRON_RUN_AS_NODE` 下 `MODULE_NOT_FOUND`；`headless` 命中是 Playwright | ★★ `~/.zcode/cli/rollout/*.jsonl`（逐请求 usage，最强观测） |
| **WorkBuddy** | ❌ **MSIX 桌面应用，无 CLI** | — | ❌ **不能接**（只有 `WorkBuddyShareTarget` 打包结构） | — |
| *ocx（代理，非 harness）* | `ocx status` / `ocx usage` | — | ✅ 在跑（PID 15896，端口 10100），**8 个 provider** | ★ `ocx usage`：30 天 5,119 次请求 / 19.8 亿 tokens / 估 **$8.79** |

### 1.2 选型结论（v0.2 修正版：执行方改为 Codex）

> **修订记录（2026-10-29 23:4x）**：初版把执行方定为 DSH，理由是"只有它带 fs+bash 沙箱"。
> **这个理由站不住**：Codex 同样有内核沙箱；而且用户的实际使用经验是 **Codex 执行能力更强**。
> 我之前只有"跑通一次单句"的证据，没有执行质量的基准——把"沙箱有无"当成排除条件是我的错误权重。
> 更要紧的是我漏了一个事实：**Codex 的 `-m` 能指向 ocx 收录的任意模型**（含 `anthropic/claude-sonnet-5`、
> `google/gemini-3.5-flash`、`nvidia/nemotron:free`），所以"Codex 执行就只能同族验收"这个前提也不成立。

**执行方 = Codex**（采纳用户的判断）。**验收方 = Pi（首选）/ Codex 换模型（立即可用）**。

| 角色 | 选谁 | 为什么 |
|---|---|---|
| **执行方** | **Codex**（`deepseek-v4-flash` 经 ocx） | ① 用户实测经验：执行能力比 DSH 强；② 有内核沙箱（`workspace-write`）；③ `codex exec` 为自主长活设计；④ 实测通（exit 0，单次 5,149 tokens） |
| 执行备选 | DSH `headless` | 有 fs+bash 沙箱、已验证可无人值守；**与 Codex 同属 DeepSeek 系**，只在 Codex 不可用时用 |
| **验收方（首选）** | **Pi** + `bigmodel/glm-4.7-flash` | ① 只读工具面 `--tools read,grep,find,ls`；② context 仅 1,358 tokens；③ **跨厂商**（GLM）**且跨脚手架**——双盲最强；④ `--mode json` |
| **验收方（立即可用）** | **Codex 换模型**：`-m anthropic/claude-sonnet-5` 或 `-m google/gemini-3.5-flash` | 无需新凭证，ocx 已收录；**跨厂商但同脚手架**，双盲次强 |
| 明确不要 | DSH 当验收方 | 与 Codex 执行**同模型族**（DeepSeek）+ 同走 ocx → 等于自证，双盲最弱 |
| 明确不要 | Pi 当执行方 | 无沙箱、无权限确认 |
| 明确不要 | Claude Code | 端点失效；**不要为它做适配器** |
| 明确不要 | ZCode / WorkBuddy | 拿不到 headless |

**双盲强度矩阵**（这是选型的真正依据，不是"能不能跑"）：

| 执行 | 验收 | 模型族 | 脚手架 | 强度 | 备注 |
|---|---|---|---|---|---|
| Codex / deepseek | Pi / GLM | 不同 | **不同** | ★★★ | 首选 |
| Codex / deepseek | Codex / claude-sonnet-5 · gemini | 不同 | 相同 | ★★ | **Pi 缺凭证时的默认回退** |
| Codex / deepseek | DSH / deepseek | **相同** | 不同 | ★ | 只在没有别的选择时 |
| Codex / deepseek | Codex / deepseek | 相同 | 相同 | ✗ | 等于自己审自己，禁止 |

**⚠️ 实测修订（2026-10-29 23:5x）：`ocx models` 的目录 ≠ 实际可用。**
逐个实探 `/v1/chat/completions` 的结果：

| 模型 | 结果 |
|---|---|
| `nvidia/nemotron-3.5-lightning:free`（openrouter） | ✅ 通（免费档，**同样有拥堵风险**） |
| `anthropic/claude-sonnet-5`（openrouter） | ❌ 403 区域封锁 |
| `openai/gpt-6-luna`（openrouter） | ❌ 无可用的 OpenAI 账号凭证 |
| `google/gemini-*`（google 直连） | ❌ provider 未配 key |
| `moonshotai-cn/kimi-*`、`minimax/*:free` | ❌ 被路由到只认 `deepseek-flash / deepseek-v4-pro` 的端点 |
| `deepseek-flash` / `deepseek-v4-pro` | ✅ **唯一稳定可用**（也正是 4,957 次请求的来源） |

**结论：当前"跨厂商"实际上拿不到稳定通道**，因此 ——

| 档位 | 执行 | 验收 | 2026-10-29 00:0x 实测 |
|---|---|---|---|
| ★★★ | Codex/deepseek | Pi/GLM | ❌ 智谱 key 未充值、免费档拥堵 |
| **★★** | **Codex/deepseek** | **Codex + `-s read-only` + `nvidia/nemotron-3.5-lightning:free`** | ✅ **PASS 12.70s —— 当前默认** |
| ★ | Codex/deepseek | DSH/deepseek | ⚠️ **FAIL**：`MISSING_CREDENTIAL: llm-deepseek`，需 `DEEPSEEK_API_KEY` 在环境里 |
| ✗ | Codex/deepseek | Codex/deepseek | 禁止（**同模型** = 自己审自己） |

**关键实测结论：默认验收方改为 `Codex + -s read-only + 非 deepseek 模型`（★★）。** 三条理由：

1. **它现在就能跑**（DSH 缺 `DEEPSEEK_API_KEY`、Pi 缺智谱 key）；
2. **`-s read-only` 是工具级强制的只读沙箱**——"验收方不许改文件"这条铁律由沙箱保证，
   而不是靠提示词嘱咐。这比任何提示都可靠；
3. **跨模型族**才是双盲的实质；脚手架相同只是次优（同一套系统提示的偏差仍在），
   但比"同模型"强一个量级。

> **因此铁律 4 改口径：禁止的是"执行与验收用**同一个模型**"，而不是"同一个脚手架"。**
> 不同模型 + 只读沙箱 = 合格；同模型 = 自证，禁止。

**所以 v0.2 的现实默认档是 ★：Codex 执行 + DSH 验收。**
不同脚手架、同模型族——**弱双盲，但它是唯一可靠的**。它仍能拦住一批执行方自己的盲区
（不同系统提示、不同工具约定、不同上下文装配），拦不住的是模型本身的固有偏差。

**升档只需一件事**：一把**可靠的、非 DeepSeek 的**付费/免费配额 key，填进 ocx 的 `google` 或 `openrouter` provider。

| 方案 | 成本 | 升到 | 说明 |
|---|---|---|---|
| Google AI Studio 免费 key（`generativelanguage`）→ ocx `google` provider | **免费**（有速率限制但不拥堵） | ★★ 或 ★★★ | **最推荐**：10 分钟申请，`gemini-3.5-flash-lite` 够验收用 |
| OpenRouter 充 $5 → `openai/gpt-*` | ~¥36 | ★★ | 稳定，但仍有区域限制风险 |
| 智谱充值 | 少量 | ★★★（Pi/GLM） | Pi 的 `models.json` 已配好，充值即通 |

**补充（2026-10-29 23:5x，用户反馈）：CodeArts 与 WorkBuddy 都有积分额度，能跑免费的 DeepSeek V4.1 Flash。
但两者都是 GUI 形态（CodeArts 是 VS Code 系 Electron、WorkBuddy 是 MSIX 桌面应用），
且在这两个应用的数据目录里**没有找到可被复用的 API 端点**（WorkBuddy 只落了 `memory/*.md`，
额度记在服务端）。所以：

> **有额度 ≠ 能编排。GUI 工具必须有人在旁边，而"人在旁边"正是本工具要消除的东西。**

它们的额度有正确用法：**需要边看边判断的手动任务**交给它们；
**能写死验收标准的自动化任务**交给 `ag`（Codex 执行 + DSH 验收）。这样 Codex 的额度只花在无人值守的活上。

**`ag doctor` 必须打印当前档位**（★★★ / ★★ / ★），让人随时知道自己在哪一档——
这是本期设计里唯一必须暴露给使用者的"质量信息"。

**执行模型**：`deepseek-v4-flash`（当前 Codex 默认，便宜、快）。

**Pi 的前置条件**：`~/.pi/agent/auth.json` 目前是空的，`ZHIPU_API_KEY` 在用户级环境也不存在。
两种补法——

```bash
# 方式 A：用户级环境变量（注意 agent 子进程可能不继承，被编排时必须显式传）
[Environment]::SetEnvironmentVariable('ZHIPU_API_KEY','<智谱按量 key>','User')

# 方式 B：命令行显式传（更可靠，适合编排器调用）
pi -p --model bigmodel/glm-4.7-flash --api-key <key> --mode json ...
```

**Pi 没配好之前，验收方就用 `Codex -m google/gemini-3.8-flash`**——功能完全一样，只是双盲从 ★★★ 降到 ★★。
**这条降级必须写进 `ag doctor` 的输出**，让人知道当前在哪一档。

可用模型（本机 `models.json` 已配 `bigmodel` 通道）：`bigmodel/glm-4.7-flash`（标价免费）→ 首选；`bigmodel/glm-5.3-flash`（¥0.8/¥2.8 每 M）→ 需要更强时用。

> ⚠️ 表中「待探」项不是偷懒，是**设计必须包含 Phase 0 探测**：非交互行为、退出码语义、错误串格式只能实跑才知道。**但执行方与验收方的选型不再待探**——上面已经用实测把范围收窄到 Codex / Pi / DSH 三个。

### 1.1 单独说 ZCode（2026-10-29 实测，结论未定但有明确下一步）

**产品形态**：Electron 桌面应用（`ZCode.exe` 222 MB），内部带一个 CLI bundle：
`resources/app.asar` → `apps/zcode-cli/packages/cli/dist/zcode.cjs`，并配 `bundled-skills/`。

**headless 入口：未证实。** 已试过的两条路：

| 路径 | 结果 |
|---|---|
| `ELECTRON_RUN_AS_NODE=1 ZCode.exe <asar 内 zcode.cjs> --help` | **机制成立**（跑出了 Node v24.14.0），但**读不到 asar 内模块**（`MODULE_NOT_FOUND`）——asar 的 fs 挂载只在 Electron 正常初始化时生效，RUN_AS_NODE 模式不带 |
| 在 `app.asar` 里找 `--print` / `non-interactive` 之类的 CLI 开关 | `--print` 的 4 次命中是 Emacs 的 lisp 符号表，无关；`headless` 的 217 次命中是 **Playwright 浏览器 headless**，也是误读。**没有找到 CLI 的一次性模式开关** |

**P0 阶段要验证的三条候选路径**（按可能性排序）：

1. 确认 ZCode 是否另有独立 CLI 安装包（查官网/更新器 `app-update.yml` 指向 `http://localhost:8081`，像是内网分发，可能有单独的命令行组件）；
2. 用 asar 提取（Python 读 asar header → 按 offset 抽出 `zcode.cjs` 及其 `node_modules` 依赖）到临时目录，再用 `ELECTRON_RUN_AS_NODE` 跑——**可行但要处理 asar 内 `require` 的相对路径**；
3. 若以上都不通，**ZCode 不进「执行方/验收方」名单，只进「观测源」名单**（见下）。

**它的真正价值：观测最强，强于 ocx。**

`~/.zcode/cli/rollout/model-io-session_*.jsonl` 是**逐请求**的模型 I/O 记录，实测字段：

```json
{"completedAt":"...","durationMs":29138,"requestId":"...","attempt":1,
 "model":{"modelId":"GLM-5.3-Flash","providerId":"account:zai-start-plan"},
 "request":{"body":{...}},
 "usage":{"input_tokens":16587,"output_tokens":1556,"cache_read_input_tokens":22016},
 "...":{"inputTokens":38603,"outputTokens":1556,"totalTokens":40159,"cacheReadTokens":22016,"cacheWriteTokens":0}}
```

同一条记录里 `usage` 有两种命名（snake 与 camel），解析时**两种都要认**。本机实测到的 model 是 `GLM-5.3` / `GLM-5.3-Flash`，provider `account:zai-start-plan`（智谱 z.ai 套餐制）。

**结论**：即使 headless 打不通，ZCode 也能当 AgentGate 的**成本/额度观测适配器**——按请求累计 token，比 `ocx usage` 的粒度更细（带 cache 读写）。这条能力与它能否当 harness 无关，**先拿下**。

---

## 2. 核心原理：为什么可以不要你这个中间人

### 2.1 现在的回路

```
人给执行方下指令 → 执行方干完 → 执行方写一段"执行摘要" → 人读摘要、转述给验收方
→ 验收方基于"转述"审 → 验收意见 → 人转述回执行方
```

这条链上有两个损耗点：**摘要会省略**（执行方倾向报喜）与**转述会失真**（你多一层理解）。

### 2.2 改造后的回路

```
人给一次任务规格 → 编排器起 worktree → 执行方在 worktree 里干
→ 编排器只收集【工件】：git diff + 测试/命令的原始 stdout + 退出码 + 变更文件清单
→ 验收方在【同一个 worktree】上独立复核，只输出结构化 verdict
→ 编排器解析 verdict：PASS 则等人工合并；FAIL 则把 defect 列表回灌给执行方，进入下一轮
```

**关键设计：验收方的输入里不包含执行方的"执行摘要"。**

这是整个方案的立足点。理由：执行摘要的用途是给人看的，不是给验证用的；把它塞给验收方，等于把"自述"当"证据"。验收方要看到的是：

- `git diff`（改了什么，逐行）
- 验收清单（任务规格里事先写死的、可执行的标准）
- 原始命令输出（测试、lint、自定义脚本）
- 文件清单与行数统计

**验收方自己判断**这些是否满足清单，**不需要**执行方告诉它"我做了什么"。

### 2.3 双盲与反自证

- 验收方**不知道**执行方是谁（不传 harness 名、不传模型名），避免"同族偏好"。
- **执行方与验收方必须是不同的 harness**（理想情况不同厂商），否则等于自己审自己。编排器强制校验：`executor.harness != verifier.harness`。
- 验收方的 verdict 里**每条 defect 必须带一条可执行复现命令**，编排器会真的去跑它（第 4 轮之后的做法，MVP 可先只记录）。

---

## 3. 架构

### 3.1 组件

```
┌───────────────────────────────────────────────────────────────┐
│  人：写 task.yaml（一次，3–5 分钟）  ← 唯一需要人参与的输入   │
└───────────────────────────┬───────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│  Orchestrator（一个 Python CLI：`ag`）    —— 之后人不在场       │
│                                                               │
│  TaskSpec → WorkspaceMgr → Runner ──────────► Collector       │
│                 (worktree)  (codex 执行)     (只收工件)        │
│                                  ▲               │            │
│                                  │ 回灌 defect   ▼            │
│                              Arbiter ◄────── Verifier         │
│                            (轮次/收敛)      (pi --mode json)   │
│                                  │                            │
│                                  ▼                            │
│                       Marker + report.md                      │
│                  (DONE / FAILED / BLOCKED，等你来看)           │
└───────────────────────────────────────────────────────────────┘
        ✗ QuotaGovernor / Notifier —— v0.2 不实现（见附录 A）
```

| 组件 | v0.2 做吗 | 职责 | 关键设计 |
|---|---|---|---|
| **TaskSpec** | ✅ | 读 `task.yaml`：目标、验收清单、约束、预算、轮次上限 | 验收清单必须**逐条可执行**（一条命令 + 期望输出），不接受"代码质量良好"这种话 |
| **WorkspaceMgr** | ✅ | `git worktree add` 一个独立工作树 + 分支 `ag/<task>-r<N>` | 每轮一个 worktree，失败即弃，成功等人工合并 |
| **Runner** | ✅ | 按 adapter 起对应 harness 的非交互进程 | 统一接口：`run(prompt, cwd, timeout, env) -> RunResult` |
| **Collector** | ✅ | 只收工件：diff、命令输出、退出码、文件清单、会话 jsonl 路径 | **不收**执行方的自然语言总结（不保存、不给验收方） |
| **Verifier** | ✅ | 用另一个 harness + `--json-schema` 输出结构化 verdict | 输入只有工件 + 验收清单 |
| **Arbiter** | ✅ | 状态：轮次、收敛判定、`DONE/FAILED/BLOCKED` 标记 | 同一 defect 连续两轮出现 → 停 `BLOCKED`，不硬闯 |
| **Marker/Report** | ✅ | 写 `runs/<id>/{state.json,DONE,FAILED,BLOCKED,report.md}` | **替代通知**：不推送，你自己 `ls runs/*/DONE` |
| ~~QuotaGovernor~~ | ❌ v0.3 | 额度与降级链 | 见附录 A，v0.2 一律"停下写 BLOCKED" |
| ~~Notifier~~ | ❌ v0.3 | 桌面通知 + `alerts/` | v0.2 用标记文件，不打扰 |

### 3.2 目录布局

```
D:/Projects/agentgate/
├─ ag.toml                    # v0.2 只有：executor/verifier 各一行、默认模型、全局超时
├─ adapters/                  # v0.2 只做 3 个，写死，不做插件系统
│   ├─ codex.py  pi.py  dsh.py   # 执行 / 验收 / 执行备选
│   └─ _probe.py              # P0 用：探测三者真实行为，打印能力表
├─ ag/                        # 编排器本体
│   ├─ spec.py workspace.py runner.py collector.py
│   ├─ verifier.py arbiter.py report.py cli.py
│   └─ （无 quota.py / notify.py —— 见附录 A，v0.3 再说）
├─ schemas/
│   ├─ task.schema.json
│   └─ verdict.schema.json    # 验收方输出受此约束
├─ runs/
│   └─ 2026-10-30-jobtempo-m1-01/
│       ├─ task.yaml
│       ├─ round-1/{diff.patch, artifacts/, executor.log, verdict.json}
│       ├─ round-2/…
│       ├─ state.json         # 可恢复：进程崩了 `ag resume <run>`
│       ├─ report.md          # 一页纸（§10.3 格式）
│       └─ DONE | FAILED | BLOCKED   # 状态标记，替代通知
└─ templates/                 # §10.1 的四个 task.yaml 模板
```

**`state.json` + `ag resume` 是必须的**：整个流程可能跑 40 分钟，你的机器会休眠、代理会掉、harness 会挂。没有恢复能力的编排器等于一次性的。

---

## 4. 接口设计

### 4.1 `task.yaml`（人写的唯一文件）

```yaml
id: jobtempo-c25-schema-parity
repo: D:/Projects/jobtempo
goal: |
  给 opportunity 表补 p_success 与 must_gap 两列（migration v3），
  补齐读写路径，并加一条 schema/model 字段一致性测试。
executor:
  harness: codex          # 谁执行
  model_chain: [deepseek-chat, qwen-max]   # 同 harness 内的模型降级链
verifier:
  harness: pi             # 谁验收（编排器强制 != executor.harness）
  model_chain: [sonnet, haiku]
constraints:
  - 不碰 src/jobtempo/plan/ 与 match/
  - 不 push
checks:                   # ★ 验收清单：每条必须可执行
  - name: pytest
    run: uv run pytest -q
    expect: {exit: 0, stdout_contains: "passed", stdout_not_contains: "failed"}
  - name: ruff
    run: uv run ruff check src tests && uv run ruff format --check src tests
    expect: {exit: 0}
  - name: parity-test-exists
    run: grep -q "test_schema_model_field_parity" tests/*.py
    expect: {exit: 0}
  - name: column-exists
    run: uv run python -c "import sqlite3;c=sqlite3.connect('data/private/jobtempo.sqlite');print([r[1] for r in c.execute('PRAGMA table_info(opportunity)')])"
    expect: {stdout_regex: "p_success.*must_gap|must_gap.*p_success"}
budget:
  max_rounds: 3
  max_minutes_per_round: 25
  max_total_minutes: 90
human_approval:
  before_execute: false    # 是否开跑前要人确认一次
  before_merge: true       # 合并前必须人确认
```

### 4.2 Adapter 契约（每个 harness 一个文件，约 60–100 行）

```python
class Adapter(Protocol):
    name: str
    def capability(self) -> dict:      # 支持哪些：json输出/结构化schema/续跑/只读模式
    def build_cmd(self, prompt: str, model: str, *, cwd: Path,
                  readonly: bool, schema: Path | None) -> list[str]: ...
    def parse(self, proc: CompletedProcess, *, workdir: Path) -> RunResult: ...
```

`RunResult` 必须包含：

```python
@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    transcript_path: Path | None   # 会话 jsonl
    usage: TokenUsage | None       # 有就填
    error_class: ErrorClass        # OK / QUOTA / RATE_LIMIT / AUTH / NETWORK / TIMEOUT / TOOL_ERROR / UNKNOWN
```

**`error_class` 是整个降级链的开关**，它的判定只能靠「退出码 + stderr 模式匹配」，见 §6。

### 4.3 `verdict.json`（验收方输出，受 JSON Schema 约束）

```json
{
  "verdict": "FAIL",
  "summary": "p_success 列已加但读写路径未接，opp-001 仍读 0.05",
  "checks": [
    {"name": "pytest", "status": "PASS", "evidence": "114 passed"},
    {"name": "column-exists", "status": "PASS", "evidence": "['...','p_success','must_gap']"},
    {"name": "roundtrip", "status": "FAIL", "evidence": "jt opp show opp-001 显示 0.05"}
  ],
  "defects": [
    {
      "id": "D1",
      "severity": "blocking",
      "where": "src/jobtempo/db.py:upsert_opportunity",
      "why": "INSERT 列表未包含 p_success / must_gap",
      "reproduce": "uv run jt opp show opp-001 | grep 成功率   # 期望 0.07 实际 0.05",
      "expect": "0.07",
      "actual": "0.05"
    }
  ]
}
```

**硬规则**：`verdict == PASS` 必须满足「所有 checks 全 PASS 且 defects 为空」。编排器不采信 `summary` 的文字，只信 `verdict` + `defects`。这样验收方就没法用一段漂亮话蒙过去。

---

## 5. 隔离、回滚与合并

1. 每个 run 在 `git worktree add ../jobtempo-ag-r1 -b ag/<task>-r1` 里跑。主工作树**始终干净**。
2. 失败轮次：`git worktree remove --force` + 删分支，或保留到 `runs/<id>/round-N/worktree` 供人复盘（推荐保留，磁盘便宜）。
3. PASS 后不自动合并：`ag` 打印 `git merge --no-ff ag/<task>-r1` 的确切命令，**人点**。
4. 每轮 `diff.patch` 落盘，验收方读的是 patch 文本而不是"执行方的描述"。

---

## 6. 【附录 A】额度与降级（**v0.2 不实现**，留作 v0.3 参考）

> ⚠️ **本节全部在 v0.2 范围之外，实现方请整节跳过。** §9「明确不做」点名了这里的每一项。
> v0.2 对额度问题只有一条规则：**非 0 退出 / 报无额度 → 停下写 `BLOCKED` 标记，等人手动换 harness 重跑。**
>
> **为什么不做**：跨 harness 自动降级要写错误分类器（会误判）、三层额度观测（无统一接口）、切换状态机（切完要重跑已做的工作）、以及兜底——**约 15–20 小时**。而它替代的操作是"手动换 harness"，一个月两次、每次 2 分钟。

### 6.1 降级链（设计留档）

```
executor:  harness=dsh  → model_chain[0] 失败(QUOTA) → model_chain[1] … 全耗尽
           ↓
           harness 链下一个：codex → claude → pi → codearts
           ↓
           全部耗尽 → Notifier 告警 + 状态置 BLOCKED（不再重试，避免空烧）
```

- **L1 同 harness 切模型**：命令行 `-m/--model`（各 harness 参数不同，adapter 里映射）。
- **L2 跨 harness**：`ag.toml` 里配 `harness_chain`，按顺序尝试。
- **L3 全耗尽**：写 `alerts/2026-10-29-2210-all-exhausted.md` + 桌面通知 + 状态 `BLOCKED`。

**重要**：验收方失败也要有降级链（它也可能额度耗尽）。但**跨 harness 降级后必须满足"验收方 ≠ 执行方"这条不变式**——如果只剩同一个 harness 可用，则判定 `BLOCKED`，不许自己审自己。

### 6.2 错误分类表（判定信号 → 动作）

| 类别 | 判定信号（stderr / 退出码） | 动作 |
|---|---|---|
| `QUOTA` | `insufficient_quota`、`quota exceeded`、`余额不足`、`credit`、`billing`、`402` | **切模型** → 切 harness |
| `RATE_LIMIT` | `429`、`rate limit`、`too many requests`、`TPM`/`RPM` | **等待 Retry-After** 或指数退避（不换模型） |
| `AUTH` | `401`、`invalid api key`、`unauthorized`、`login required` | **不重试**，直接告警（换模型也没用） |
| `NETWORK` | `ECONNREFUSED`、`ETIMEDOUT`、`tunnel error`、`getaddrinfo` | 重试 2 次；仍失败则告警"检查代理" |
| `TIMEOUT` | 超过 `max_minutes_per_round` | 重试 1 次；仍超时 → BLOCKED |
| `TOOL_ERROR` | harness 正常退出但内部工具失败 | 不降级，把错误原文回灌给执行方 |
| `UNKNOWN` | 以上都不匹配 | **不自动降级**，保存完整 stderr，告警给人（自动猜会掩盖真 bug） |

> `UNKNOWN` 不自动降级这条是刻意的。上个月那种"GitHub 直连失败就归因成 GitHub 被墙"的误判，就是自动降级的副作用——**宁可停下来问人，也不要猜着换一个可能更差的路径**。

### 6.3 额度观测的三层（诚实说明：没有统一接口）

**不存在"统一查询所有 provider 剩余额度"的接口。** 只能三路拼：

| 层 | 数据源 | 精度 | 说明 |
|---|---|---|---|
| L1 硬信号 | provider 返回的错误体 | 100%（已耗尽时才给） | 只能知道"已经没额度"，不能提前 |
| L2 用量累计 | Codex 走 `ocx usage`（token + 估算成本）；Claude `--output-format json` 带 usage；**ZCode 的 `~/.zcode/cli/rollout/*.jsonl` 逐请求带 usage（含 cache 读写）** | 高（每次调用后） | 需要每次运行后落盘累计 |
| L3 会话统计 | 各 harness 会话 jsonl 里的 usage 字段（DSH `--json` 事件流、Codex `sessions/**.jsonl`） | 中（取决于格式） | 兜底 |

**实现顺序建议**：先把 L3 接上（每个 harness 的 jsonl 都能解析出 token），跑一周拿到真实消耗曲线；再接 L2（`ocx usage` / ZCode rollout）校准成本；L1 在降级链里天然就有。**别一开始就追求精确金额**——先用 token 数做相对比较，已经足够触发"额度快用完了"的判断。

### 6.4 付费额度提醒

`budget.yaml`（人来填，一次）：

```yaml
providers:
  deepseek:   {monthly_cny: 50,  warn_at: 0.80}
  anthropic:  {monthly_usd: 20,  warn_at: 0.80}
  openai:     {monthly_usd: 10,  warn_at: 0.90}
free_providers: [ollama, siliconflow-free]   # 不告警，只记录
```

- 每次 run 结束后把 usage 累加进 `usage.db`（sqlite）。
- 达到 `warn_at` → 一次提醒（**不重复刷**，同一 provider 同一周期只提醒一次）。
- 达到 100% → 该 provider 加入降级链黑名单，直到下个周期或人手动解除。
- **提醒内容必须给决策依据**：「deepseek 本月已用 ¥41/¥50（82%）。剩余额度约够 6 次同级任务。是否切换到 codex（走 ChatGPT 订阅，边际成本 0）？」

---

## 7. 人能被打扰的时刻（v0.2：不推送，只看标记）

**v0.2 没有通知器。** 编排器只做两件事：**跑**、**在 `runs/<id>/` 里留下状态标记**。你有空时 `ls runs/*/` 一眼就知道该看哪个。

| 状态标记 | 含义 | 你需要做什么 |
|---|---|---|
| `DONE` | 验收 PASS，分支待合并 | 看 `report.md` → 审 diff → 决定合并（5 分钟） |
| `FAILED` | 轮次跑满仍不过 | 看 `report.md` 的 defect 列表，判断是规格问题还是能力问题（5 分钟） |
| `BLOCKED` | 遇到无法自解的问题（额度/权限/连续同 defect/超预算） | 看 `report.md` 最后一节，手动处理（通常是换个 harness 或补个前提） |
| 无标记 | 正在跑，或进程已死（用 `ag resume` 续） | 先 `ag resume`，再看有没有标记 |

**四个"必须人决定"的时刻**（v0.2 仍然保留，只是形式从"推送通知"变成"标记等你"）：

1. **开跑前**：写 `task.yaml`（唯一无法外包的一步，3–5 分钟，模板见 §10.1）
2. **轮次跑满仍 FAIL**：标记 `FAILED`，等你判断方向是否错了
3. **`BLOCKED`**：等你换 harness 或补前提
4. **合并前**：`before_merge: true` 永不自动合并，`ag` 只打印 `git merge --no-ff ...` 命令

> 这条是设计里最容易被做坏的部分。很多编排器乐于"实时汇报"，结果把人的注意力切得比手动更碎。**v0.2 的原则：能写文件就不要说话。**

---

## 8. 为什么不用现有的东西

| 现成方案 | 能解决什么 | 为什么不够 |
|---|---|---|
| **DSH Agent Teams / `spawn_teammate`** | 同一 harness 内多角色并行、Lead 汇总 | **跨不了 harness**；执行与验收同宿主同模型族，等于自证 |
| **`codex review`** | 非交互审 diff | 只审 diff 文本，不跑你的验收命令、不读任务规格、输出自然语言非结构化 |
| **`pi --mode json`** | 结构化输出 + 只读工具面 | 只是"一个可收窄工具面、能出 JSON 的 harness"，本身不编排 |
| **CI（GitHub Actions）** | 跑测试 | 无 remote 跑不了；且它只跑命令，不做语义验收 |
| **人肉转述（现状）** | 无需开发 | 你在回路里，注意力被切碎，且转述必然失真 |

结论：**没有现成的**。但也不必从零——`pi --mode json`（配 `--tools read,grep,find,ls`）当验收方的结构化输出层、`ocx usage` 当成本观测层，这两块直接复用。

---

## 9. 工期估算（v0.2 砍定版）

| 阶段 | 内容 | 产出与验收 | 估时 |
|---|---|---|---|
| **P0 探测** | **只探 3 个**：Codex `exec`（执行）、Pi `-p --mode json`（验收）、DSH `headless`（执行备选）。记录确切命令行、退出码语义、错误串、会话落盘路径 | `adapters/_probe.py` 打印三行能力表 + `ag doctor` 能一次性探完三者状态 | **1h** |
| **P1 工件与工作树** | task.yaml 解析、`git worktree`、Runner、Collector、`runs/<id>/` 落盘、`ag run --dry-run` | `--dry-run` 建好 worktree 并打印将执行的命令，**零 API 调用** | **2–3h** |
| **P2 验收闭环** | Claude `--json-schema` 出 verdict、Arbiter 状态机、defect 回灌、轮次上限、`state.json` + `ag resume` | 真的跑一个 JobTempo 小任务：第 1 轮 FAIL → 第 2 轮 PASS → 待合并分支 | **2–3h** |
| **P3 无人值守硬化** | 硬预算（轮次/分钟/金额）、超时、`DONE`/`FAILED`/`BLOCKED` 标记文件、一页纸 `report.md`、崩溃后 resume 实测 | kill 进程后 `ag resume` 能续；跑完 `runs/<id>/report.md` 一页纸可读 | **1–1.5h** |
| 合计 | | | **6–8.5h** |

**明确不做**（v0.1 里的 P3「额度与降级」整块砍掉）：跨 harness 自动降级、`budget.yaml` 成本治理、桌面通知、pi / CodeArts / ZCode 适配器。
理由：这些在"省在场"目标下的边际收益极低——手动换 harness 一个月两次，写 20 小时不划算。

### 9.1 但必须加一个小小的东西：`ag doctor`（preflight 健康检查）

**为什么加**：2026-10-29 实测，Claude Code 一次普通调用**卡死 240 秒**才发现端点挂了。无人值守跑的编排器不能这样——它必须**在开跑前 10 秒内**知道"这次跑不跑得动"，跑不动立刻 `BLOCKED`，而不是让你 4 小时后回来看见一个挂死的进程。

```bash
ag doctor          # 探三个后端 + 报结论，约 10 秒
```

| 探测项 | 命令 | 判定 |
|---|---|---|
| ocx 代理 | `ocx status` | 有 `Proxy: running` 即可（可选，只有 Codex 依赖） |
| 执行方 Codex | `codex exec --skip-git-repo-check -m deepseek-v4-flash "reply OK"`（60s 超时） | exit 0 |
| 验证方 Pi | `pi -p --no-tools --no-skills --no-session --model bigmodel/glm-4.7-flash --mode text "reply OK"`（25s 超时） | exit 0 且输出含 `OK` |
| 凭证 | 检查 `ZHIPU_API_KEY` 或启动时传的 `--api-key` 是否非空 | 空 → 直接提示怎么配 |

**规则**：`ag run` 启动时**先跑 doctor**；任一项失败 → 写 `BLOCKED` + 原始错误 + 修复建议，**立即退出，不重试**。

**约 40 行代码，替代掉 15–20 小时的自动降级链**——这就是"中转不稳"这个现实下最划算的做法。*（v0.3 才考虑自动切换 provider；v0.2 一律等人。）*

**如果时间紧**：P0+P1+P2 就是可用品（5–7h），P3 的硬化可以后补。但没有 P3 的 `resume` 和 `report.md`，它就还是个"要你盯着"的工具，所以**建议一次做完**。

---

## 10. 本次范围（就是 MVP，没有更大的版本）

### 10.1 四个 task.yaml 模板（把你唯一的固定成本压到 3–5 分钟）

日常任务基本是这四类，复制对应模板改 3–4 行即可：

```yaml
# 模板 A：加功能 / 改代码
id: <项目>-<短名>
repo: D:/Projects/<项目>
goal: |
  <一段话说清要什么，含不改什么>
executor: {harness: codex, model: deepseek-v4-flash}
verifier: {harness: pi, model_chain: [bigmodel/glm-4.7-flash]}
constraints:
  - <不许碰的目录/文件>
  - 不 push
checks:
  - {name: pytest, run: uv run pytest -q, expect: {exit: 0, stdout_not_contains: failed}}
  - {name: lint,   run: uv run ruff check src tests, expect: {exit: 0}}
  - {name: <业务断言>, run: <一条可执行命令>, expect: {stdout_regex: "..."}}
budget: {max_rounds: 3, max_minutes_per_round: 25, max_total_minutes: 90}
```

```yaml
# 模板 B：只修 bug —— 与 A 的区别是 checks 里必须有"复现命令"
# 模板 C：只写测试 —— goal 写清覆盖什么，checks 用覆盖率或指定测试名
# 模板 D：只改文档 —— checks 只需 exit 0 + grep 关键断言，executor 可用便宜模型
```

**写 checks 的唯一要求**：一条命令 + 期望输出。写不出可执行的 check，说明这个任务的验收标准还没想清——**那就不该开跑**，这本身是个有用的过滤器。

### 10.2 剪掉的东西

| 砍掉 | 手动替代 |
|---|---|
| 跨 harness 自动降级 | 额度不够时 `ag` 停下写 `BLOCKED`，你改成另一个 harness 重跑（一个月两次） |
| 成本治理 `budget.yaml` | 硬预算只管"轮次 + 分钟"，金额不判断（你月度账单自己看） |
| 桌面通知 | 跑完写 `runs/<id>/DONE`，你有空 `ls runs/*/DONE` 看一眼 |
| CodeArts / ZCode 适配 | 拿不到 headless，接不进；以后真打通再加 |

### 10.3 一页纸报告（`runs/<id>/report.md`）——这是"省在场"的核心产物

```markdown
# <task-id>  <PASS/FAIL/BLOCKED>  用时 41min / 3 轮 / 约 ¥0.8

## 结论（先看这段）
<3 行以内：做成了什么，或者卡在哪>

## 每轮摘要
- R1: FAIL（2 defect）→ R2: FAIL（1 defect）→ R3: PASS
- 收敛行为：D2 出现两次（换了修法后消失）

## 改了哪些文件
<git diff --stat 输出>

## 验收证据
| check | 结果 | 关键输出 |
|---|---|---|

## 你需要做的
1. 审 `git diff`（命令已附）
2. 合并：`git merge --no-ff ag/<task>-r3`
```

**判据**：你能在 60 秒内从这份报告决定"合并 / 打回 / 不管"。

### 10.4 验收标准

拿一个真实任务（建议就用 JobTempo 的 M1 第一小步），从 `ag run task.yaml` 一条命令开始，中途不碰键盘，最后得到：

- `verdict: PASS`
- 一个 `ag/<task>-r<N>` 分支
- 一份一页纸 `report.md`
- `runs/<id>/` 里有每轮的 diff / artifacts / verdict / state

---

## 11. 风险（v0.2 重新算过）

| 风险 | 说明 | 对策 |
|---|---|---|
| **造工具吃掉本该复习的时间** | 这是真风险，不是抽象风险。6–8.5h 是一次性支出，但**返工**是黑洞：v0.1 的 30h 版本里，一半是"通用性"带来的返工（多 harness、降级链、成本治理） | 范围已砍死（§9）。**中途不允许加功能**；P3 做不完就交付 P0–P2 |
| **规格写错 → 在错的路上跑 3 轮** | 比人工更糟：人工会当场发现方向错了，自动迭代只会把错误执行得更彻底 | `max_rounds: 3` 硬上限；`report.md` 的「结论」段强制 3 行以内，逼它暴露偏差；R1 后如果你恰好看到，可以立刻停 |
| **验收方被工件骗过** | 大 diff 里漏看是常态 | verdict 每条 defect 必须带**可执行复现命令**；P2 阶段编排器只记录不自动跑（跑复现命令放到 v0.3） |
| **UNKNOWN 错误被当额度耗尽** | 乱切 harness，把可恢复错误变成一团糟 | v0.2 **根本不做自动切**：任何非 0 退出都停下写 `BLOCKED`，你手动决定。这条比"聪明地自动降级"安全得多 |
| **无人值守时跑飞（烧钱）** | 轮次跑满还在撞 | `max_total_minutes: 90` + `max_rounds` 双硬闸，超了强制停 |
| **合并自动化过度** | 摘掉你最后的把关点 | `before_merge: true` **永不实现自动合并**，`ag` 只打印命令 |

### 一个必须算的账（说清楚，你自己判断）

| | 现在 | 用 AgentGate 之后 |
|---|---|---|
| 每个任务你的在场次数 | 4–6 次（读摘要/判断/转述/读验收/决定） | **1 次**（开跑前写 spec）+ 1 次（看报告决定合并） |
| 每个任务的注意力跨度 | 4–6 段，每段都要重进上下文 | 2 段，其中 1 段只需 3–5 分钟 |
| 每天 2 个任务 | 大约 8–12 次中断 | 2–4 次中断 |

**所以它的效益不在"省时间"，在"把中断次数降一个数量级"**。如果你一天只跑 1 个任务，效益不明显；如果一天 2–4 个来回（M0 期间就是这样），效益立刻显现。

**我的判断**：按你 M0 期间的节奏（一天 3–4 个来回），这个工具**值得做**，但必须一次做完、不返工。做完之后的正确用法是——**你只写 spec 和看报告，中间的 3 轮完全不管**。

---

## 12. 实施顺序（5 步，每步都有硬验收）

> 交给实现的 Agent 时必须按这个顺序，**每步做完停下来给人验**，不许一口气做完 5 步再报。
> 理由：这 5 步里任何一步理解偏了，后面 4 步都是白做——而人验证一步只要 5 分钟。

| 步 | 做什么 | 硬验收（必须贴真实输出） |
|---|---|---|
| **1** | P0 探测：写 `adapters/_probe.py`，实跑 DSH / Claude / Codex 三个非交互模式 | 打印三行表：`harness / 确切命令 / exit / 输出长度 / 会话落盘路径`。三个都要有一次成功调用 |
| **2** | task.yaml + verdict.json 的 JSON Schema，加 `ag run --dry-run` | `--dry-run` 建出 worktree 并打印将执行的完整命令，**零 API 调用**（用 `--dry-run` 跑一次，观察无网络请求） |
| **3** | 接入 Codex 执行 + Collector | 跑一个 `echo` 级最小任务，`runs/<id>/round-1/` 里出现 `diff.patch`、`artifacts/`、`state.json` |
| **4** | 接入 Pi 验收（`--tools read,grep,find,ls --mode json`）+ Arbiter 单轮 + `ag doctor` | 手工造一个必然 FAIL 的 task，能解析出 `verdict: FAIL`、`defects` 非空且每条带 `reproduce`；`ag doctor` 能正确报告三个后端状态 |
| **5** | 轮次循环 + `ag resume` + 硬预算 + `report.md` | 中途 kill → `ag resume` 能续；跑完 `report.md` 一页纸可读；超 `max_rounds` 时写 `BLOCKED` 而不是继续 |

**每步都必须能独立回滚**：worktree 丢弃 + 删 `runs/<id>/`，且不碰你现有的任何项目。

**实现方不许做的事**（写进提示词）：

- 不许加 v0.1 里被砍掉的功能（降级链、成本治理、通知、多 harness 适配）
- 不许把 `Probe`/`Adapter` 做成插件系统（就三个 if/else 写死）
- 不许引入 ORM / 状态机框架 / 工作流引擎（`state.json` + `if/elif` 就够）
- 不许自动合并、不许自动 push

---

## 13. 一句话总结

**原理**：把「执行方的自述」从验收输入里拿掉，只给验收方看工件与可执行清单——这一步消掉"传达失真"；再加上轮次循环与 resume，消掉"必须在场"。

**范围**：6–8.5 小时，一对 harness，不做通用平台。**加功能就是加风险**。

**它真正买到的东西**：把"一天 8–12 次中断"降到"2–4 次"，且每次中断都能在 5 分钟内处理完。这是你能腾出整块时间复习国考的前提。
