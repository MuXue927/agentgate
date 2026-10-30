# 如何写 `task.yaml`（详细方法与准则）

> 适用版本：AgentGate v0.2
> 配套：`schemas/task.schema.json`（机器校验）、`templates/`（四类模板）
>
> **这份文档给谁看**
> - 第一次用 AgentGate 的人：读 §0.5 就能跑起来，其余按需查。
> - 已经跑过、想写复杂任务的人：§1–§4 是主体，§6 九问自检是发布前必过的一关。
> - 接手这个项目的人：§0 的机制说明是理解整套设计的最短路径。

---

## 0.5 五分钟上手（第一次用就读这段）

### 前置条件

| 项 | 检查方式 |
|---|---|
| Python 3.12 + `uv` | `uv --version` |
| `codex` CLI 在 PATH | `codex --version` |
| 代理可用（模型走本地代理） | `uv run ag doctor` 的 required 项全 PASS |
| 一个干净的 git 仓库作为目标 | `git status --porcelain` 为空 |

### 第一步：存一个最小任务

最快的办法是复制模板：

```bash
cp templates/minimal.task.yaml D:/Projects/hello-first.task.yaml
# 然后改 3 处：id / repo / goal（以及 checks 里的目标文件）
```

或者直接照抄下面这份（路径随意，**任务文件不必放进仓库**）：

```yaml
id: hello-first
repo: D:/Projects/<你的仓库>        # ← 改成你的 git 仓库绝对路径
goal: |
  在仓库根目录新建 hello.txt，内容恰好是 ok（不含多余空行）。
  不要修改其他任何文件。
executor: {harness: codex, model: deepseek-v4-flash, sandbox: danger-full-access}
verifier: {harness: codex, model: nvidia/nemotron-3.5-lightning:free, sandbox: danger-full-access}
constraints:
  - 只允许新建 hello.txt。
  - 不要 push 或 merge；不要执行 git commit 或 git add。
checks:
  - name: hello-content
    run: python -c "import pathlib,sys; p=pathlib.Path('hello.txt'); t=p.read_text(encoding='utf-8').strip() if p.is_file() else '<missing>'; print('content:',t); sys.exit(0 if t=='ok' else 1)"
    expect: {exit: 0}
budget: {max_rounds: 2, max_minutes_per_round: 5, max_total_minutes: 10}
human_approval: {before_execute: false, before_merge: true}
```

### 第二步：干跑（零 API 成本）

```bash
cd D:/Projects/agentgate
uv run ag run --dry-run D:/Projects/hello-first.task.yaml
```

会打印：双盲档位、worktree 路径、分支名、**将发给执行方的完整命令**。确认它要改的是你预期的仓库，再往下走。

### 第三步：真跑

```bash
uv run ag run D:/Projects/hello-first.task.yaml
```

约 1–2 分钟、约 $0.01。中途不用管。

### 第四步：看结果并合并

```bash
cat runs/hello-first/report.md        # 一页纸：做了什么、改了几个文件、能不能合
git merge --no-ff ag/hello-first-r1   # 合并是人的动作，ag 永不自动合并
uv run ag clean hello-first           # 清理 worktree 与分支
```

### 成功的标志（三条全中才算跑通）

1. `runs/hello-first/DONE` 存在（不是 `FAILED` / `BLOCKED`）
2. `report.md` 的结论段写着"建议合并"，且列出改动的文件
3. `git show ag/hello-first-r1 --stat` 里有你预期的那一个文件

如果卡住了：**先看 §7 失败诊断速查**，再看 `runs/<id>/round-1/executor.log`（执行方原始输出）。

---

## 0. 先理解机制，再谈写法

写 YAML 之前必须知道**执行方到底看得见什么**，否则会写出"人觉得清楚了、机器却不知道"的任务。

| 内容 | 执行方（Codex）看得到 | 验收方看得到 | 说明 |
|---|---|---|---|
| `goal` | ✅ 全文 | ✅ 全文 | 唯一的意图来源 |
| `constraints` | ✅ 全文 | ✅ 全文 | 越界与时序禁令 |
| 每条 check 的 `name` | ✅ | ✅ | 只是标签 |
| 每条 check 的 `run` | ✅ 命令原文 | ✅ 命令原文 | 所以**关键信息要写进命令里** |
| 每条 check 的 `expect` | ❌ | ✅ | 执行方不知道期望值 |
| `budget` | ❌ | ❌ | 只影响编排器 |
| 上一轮的 defects | ✅ 第 2 轮起 | — | 回灌时给全文 |

**由此得到第 1 条准则：**

> **`expect` 是给验收方用的，Agent 看不见。凡是"必须产出什么"的信息，要么写进 `goal`，要么藏进 `run` 命令本身。**

例：`run: python -c "import pathlib; assert pathlib.Path('docs/index.md').is_file()"` —— 执行方读这条命令就知道要建 `docs/index.md`。若写成 `run: test -f $FILE`，它永远猜不到 `$FILE` 是什么。

**第 2 条准则**：AgentGate 在**隔离 worktree** 里执行（目标仓库内 `wt/<task>-r<N>`），跑完把改动提交到 `ag/<task>-r<N>` 分支。**它不 push、不自动合并**——合并永远是人做。

---

## 1. 九个必填字段 + 一个可选字段

`id / repo / goal / executor / verifier / constraints / checks / budget / human_approval` 全部必填，没有静默默认值。
`protected` 可选；不写就是不设保护，报告会打印 `PROTECTED: none declared`，不会静默。

### 1.1 `id`

任务唯一标识，也是 `runs/<id>/` 目录名。

```yaml
id: agentgate-docs
```

规则（schema 已强制）：`[A-Za-z0-9._-]`，不能含 `..`。
**建议**：`<项目>-<动词>-<对象>`，如 `jobtempo-import-jd`、`agentgate-docs`。
一旦跑过，`id` 就绑定了那个 run 目录；想重跑要么合并后 `ag clean`，要么换 `id`。

### 1.2 `repo`

被改的仓库的**绝对路径**。

```yaml
repo: D:/Projects/agentgate
```

规则与坑：

- 必须是 **git 仓库**（要 `git worktree add`）
- worktree 建在 `repo/wt/<task>-r<N>`，**必须在仓库内部**；写 `../` 会被沙箱拒绝
- `repo` 指向 AgentGate 自己时，`ag run` 默认拒绝，需显式 `--allow-self`
- 目标仓库最好**工作树干净**：脏工作树会让 diff 里混进无关改动，验收方会误判

### 1.3 `goal`

一段话讲清"要什么、不要什么"。**这是最有价值也最容易写坏的字段。**

好的 goal 有三段结构：

```yaml
goal: |
  1) 要做什么（可数、可核对）
  2) 产出在哪里（精确路径与文件名）
  3) 不要做什么（越界红线）
```

反面例子：

```yaml
goal: 优化一下这个模块的文档        # 无产出路径、无验收口径、无边界
```

正面例子（`agentgate-docs.task.yaml` 节选）：

```yaml
goal: |
  1) Move the five root-level STEP-*.md reports into docs/steps/ and rename them
     exactly like this (content unchanged, only path and filename change):
       STEP-1-报告.md -> docs/steps/step-1-doctor.md
       ...
  3) Write docs/acceptance.md: the final v0.2 acceptance record. Cover the six
     delivery criteria ... At least 800 characters.
  Do not change source code, tests, or schemas.
```

**准则**：goal 里出现的每个数字、每个文件名，都应该在 `checks` 里有一条对应断言。二者不一致时，Agent 会按 goal 做、验收按 checks 判，然后白跑一轮。

### 1.4 `executor` / `verifier`

```yaml
executor: {harness: codex, model: deepseek-v4-flash, sandbox: danger-full-access}
verifier: {harness: codex, model: nvidia/nemotron-3.5-lightning:free, sandbox: danger-full-access}
```

| 子字段 | 规则 |
|---|---|
| `harness` | `codex` / `dsh` / `pi`（当前只实现了 codex 实测路径） |
| `model` | 必填；**执行与验收必须不同模型族**，同族会被工具拒绝（防自证） |
| `sandbox` | 可选。**Windows 上必须写 `danger-full-access`**，否则 Codex 沙箱起不了进程，见 README「常见坑」 |

模型选择建议：

- **执行**：`deepseek-v4-flash`（便宜、快，6–8 分钟的长任务约 $0.01）
- **验收**：`nvidia/nemotron-3.5-lightning:free`（免费档）或任何非 DeepSeek 系
- 优先级：跨厂商 + 跨脚手架（★★★）> 跨厂商同脚手架（★★）> 同族（★，仅兜底）

### 1.5 `constraints`

字符串数组，写**禁令**。它的作用不只是防越界，也是给 Agent 的边界提示。

```yaml
constraints:
  - Only create or modify files under docs/ and move the five root-level STEP-*.md files.
  - Do not touch src/, tests/, schemas/, templates/, taskfiles/, tasks/, pyproject.toml.
  - Do not push or merge.
  - Do not run git commit or git add.
```

**必须包含的四条**（工具已强制部分，但写进 constraints 更稳）：

1. 不许 push / merge
2. 不许 `git commit` / `git add`（编排器自己负责提交，重复提交会打乱 diff 采集）
3. 明确的目录白名单
4. 不许动验收标准本身（否则 Agent 可以改 checks 蒙混）

**散文约束不能替代 `protected`。** 上面四条写进 `constraints` 只是提示词；执行方
改松测试、删掉任务文件，`constraints` 一个字都拦不住。要拦，见 §1.9。

**坑**：constraints 与 checks 冲突时任务会死锁。真实案例——原先 constraints 写"只许改 docs/"，却又有 `readme-links-still-valid` 检查 README 链接有效；移动 STEP 文件必然让 README 链接断，于是 3 轮全 FAILED。**写完后逐条对照 constraints 与 checks，确认存在解。**

### 1.6 `checks` —— 核心

数组，每项 `{name, run, expect}`，`checks` 至少一条。

```yaml
checks:
  - name: docs-exist
    run: python -c "import pathlib,sys; need=[...]; miss=[...]; sys.exit(1 if miss else 0)"
    expect: {exit: 0}
```

`expect` 四种断言，可组合（AND 关系）：

| 键 | 含义 |
|---|---|
| `exit` | 期望退出码 |
| `stdout_contains` | stdout 必须包含该文本 |
| `stdout_not_contains` | stdout 必须**不**包含该文本 |
| `stdout_regex` | stdout 必须匹配该正则 |

#### 一条好 check 的五个条件

1. **可执行**：一条命令，不需要人判断
2. **确定**：同样输入永远同样结果（不依赖时间、网络、随机）
3. **快**：单条最好 < 30 秒；检查是每轮都跑的
4. **只读**：检查命令自己不许改文件（否则会污染 diff、触发只读校验失败）
5. **可解释**：失败时 stdout 要能说出**哪里不对**（打印 `missing: [...]` 而不是裸 `assert`）

#### 写 checks 的实操顺序

先写"能立刻写出命令"的检查，写不出的部分就是还没想清的验收标准：

```
1. 跑测试        → uv run pytest -q                          expect: {exit: 0, stdout_not_contains: failed}
2. 跑 lint       → uv run ruff check                          expect: {exit: 0}
3. 断言文件存在   → python -c "...is_file()..."                expect: {exit: 0}
4. 断言内容正确   → python -c "...'关键词' in text..."          expect: {exit: 0}
5. 断言没越界     → python -c "...git status --porcelain..."   expect: {exit: 0}
6. 断言链接有效   → python -c "...re.findall(links)..."        expect: {exit: 0}
```

跨平台注意：`checks` 的 `run` 由 `shell=True` 执行，**Windows 上要用 `python -c` 或 `type`，不要写 `test -f` / `cat` / `grep`**。上面 3–6 全部用 `python -c` 就是为了跨平台。

#### 多行 `python -c` 写不下怎么办

用分号串联、`list` 推导 + `sys.exit`：

```yaml
run: python -c "import pathlib,sys; fs=list(pathlib.Path('docs').rglob('*.md')); small=[str(p) for p in fs if len(p.read_text(encoding='utf-8'))<800]; print('too small:',small); sys.exit(1 if small else 0)"
```

更复杂的检查建议写进仓库脚本（如 `scripts/check_docs.py`），YAML 里只写 `run: python scripts/check_docs.py` —— **但脚本本身也要被 constraints 允许修改**，或事先提交。

### 1.7 `budget`

```yaml
budget: {max_rounds: 3, max_minutes_per_round: 20, max_total_minutes: 60}
```

| 字段 | 含义 | 建议 |
|---|---|---|
| `max_rounds` | 最多几轮（1–10） | **默认 3**。文档/测试类 2 够用；改动大的给 3 |
| `max_minutes_per_round` | 单轮执行方超时 | 小任务 10，一般 20–25 |
| `max_total_minutes` | 全程上限 | 约等于 `max_rounds × max_minutes_per_round` |

真实成本参考：一轮「改 README + 建两个文档 + 重命名五个文件」约 2 分钟、$0.011。

### 1.8 `human_approval`

```yaml
human_approval: {before_execute: false, before_merge: true}
```

- `before_execute`: 开跑前是否要人确认（v0.2 未实装交互，保持 `false`）
- `before_merge`: **必须 `true`**。`ag` 永不自动合并，只打印 merge 命令

### 1.9 `protected`（可选）—— 受保护路径

```yaml
protected:
  - "tests/**"
  - "tasks/**"
  - "schemas/**"
```

整路径 glob，`*` 不跨 `/`，`**` 跨。**命中即终态 `BLOCKED`，不消耗轮次、不调验收方。**

判定看 git 状态，不是看内容：

| 状态 | 含义 | 处置 |
|---|---|---|
| `A` | 新增文件 | **放行**（自改任务必须能加测试） |
| `M` | 修改既有文件 | 命中 → **BLOCKED** |
| `D` | 删除文件 | 命中 → **BLOCKED** |
| `R` | 重命名 | 看**被删掉的那一侧**路径，命中 → **BLOCKED** |
| `T` / `U` | 类型变化 / 未合并 | 命中 → **BLOCKED** |

**为什么要它**：机检比对的是 `expect` 与真实输出。执行方把
`assert x == 3` 改成 `assert True`，`pytest` 照样退出 0，机检全绿。
只有"这个文件被改过"这件事，机检看不出来，`protected` 才能。

**顺序**：`collect → protected → 机检 → 验收方`。受保护检查必须排在机检之前，
否则"改松测试 → 机检全绿 → 验收官看到一片绿"就绕过去了。

**别套太死**：`tests/**` 挡住的是"改/删既有测试"。如果任务本身就要改某条旧测试，
要么别写这条 glob，要么把 glob 收窄到具体文件（如 `tests/test_contract.py`）。

---

## 2. `agentgate-docs.task.yaml` 逐段解析

这是第一次真实跑通的任务。逐段看它为什么这样写。

```yaml
id: agentgate-docs
```
→ 也是 run 目录名：`runs/agentgate-docs/`。

```yaml
repo: D:/Projects/agentgate
```
→ 目标就是 AgentGate 自己，所以运行时要 `--allow-self`。

```yaml
goal: |
  Restructure the project documentation into a complete, navigable set under docs/.

  1) Move the five root-level STEP-*.md reports into docs/steps/ and rename them
     exactly like this (content unchanged, only path and filename change):
       STEP-1-报告.md -> docs/steps/step-1-doctor.md
       ...
```
→ **枚举式 goal**：五个源文件、五个目标路径逐条列出。这样"改名规则"不需要 Agent 猜。注意它写了 `content unchanged`——避免 Agent 顺手重写报告内容。

```yaml
  2) Keep docs/design.md as the canonical design reference. Do not remove sections.
```
→ 防破坏：文件已存在，只允许加不许删。

```yaml
  3) Write docs/acceptance.md: ... At least 800 characters.
  4) Write docs/index.md as the entry point: ... At least 800 characters.
```
→ 数字门槛写进 goal，**因为 checks 里也有 800 字断言**。两处一致，Agent 不会交出空壳文件。

```yaml
  5) Update README.md so that no local link is left broken after the STEP files move.
```
→ 这条是**修死锁的关键**：原版 constraints 禁止改 README，而 checks 要求 README 链接有效，二者互斥。加这一句后任务才有解。

```yaml
  6) Add a "文件结构" section to README.md that lists every top-level directory and
     states, on the same line, what it is for. Cover at least these ten:
       src/  tests/  schemas/  templates/  taskfiles/  tasks/  docs/
       adapters/  runs/  wt/
     "runs/" and "wt/" are git-ignored runtime directories and will not exist in a
     fresh checkout -- still describe them.
     A bare name with no explanation does not satisfy this requirement.
```
→ **把隐含要求显式化**：`runs/`/`wt/` 在新 checkout 里不存在，不说清楚 Agent 会跳过它们；"光列名字不算"直接排除了偷懒写法。

```yaml
executor: {harness: codex, model: deepseek-v4-flash, sandbox: danger-full-access}
verifier: {harness: codex, model: nvidia/nemotron-3.5-lightning:free, sandbox: danger-full-access}
```
→ 跨模型族（★★）；`danger-full-access` 是 Windows 沙箱缺陷的必需绕法。

```yaml
constraints:
  - Only create or modify files under docs/, move the five root-level STEP-*.md files,
    and edit README.md to fix links and add the file-structure section.
  - Do not touch src/, tests/, schemas/, templates/, taskfiles/, tasks/, pyproject.toml, or AGENTS.md.
```
→ 白名单 + 黑名单并列，且**与 goal 第 5、6 条一致**。

```yaml
checks:
  - name: docs-exist        # 8 个文件逐个存在（文件名写死在 run 里 → Agent 看得见）
  - name: docs-substantive  # 每个 .md ≥ 800 字符（防空壳）
  - name: steps-moved-out-of-root  # 根目录不许残留 STEP-*.md
  - name: index-links-resolve      # index.md 里每个相对链接都能解析
  - name: readme-links-still-valid # README 链接不坏
  - name: readme-structure  # 10 个目录 + 标题存在 + 每行有说明
  - name: no-source-touched # git status 里不许出现 src/tests/...
  - name: no-whitespace-errors     # git diff --check
```
→ 八个检查覆盖三类：**产出对（1–2,6）**、**边界对（3,7）**、**整体不坏（4–5,8）**。

```yaml
budget: {max_rounds: 3, max_minutes_per_round: 20, max_total_minutes: 60}
human_approval: {before_execute: false, before_merge: true}
```
→ 实际用了 1 轮、2.1 分钟。

---

## 3. 五类任务模板

### 3.1 加功能

```yaml
goal: |
  Implement <功能> in <文件/模块>.
  Behavior: <输入 → 输出，边界条件>.
  Add tests covering: <正常路径>, <边界>, <错误路径>.
  Do not change <无关模块>.
checks:
  - {name: tests, run: "uv run pytest -q", expect: {exit: 0, stdout_not_contains: failed}}
  - {name: lint,  run: "uv run ruff check", expect: {exit: 0}}
  - {name: api-exists, run: 'python -c "import <pkg>; print(<pkg>.<新函数>)"', expect: {exit: 0}}
```
**要点**：让 Agent 自己写测试，但**测试的验收标准由你定**（用 `pytest` 整体通过 + 指定名字的测试存在）。

### 3.2 修 bug

```yaml
goal: |
  <复现条件> 时出现 <错误现象>，期望 <正确行为>。
  Root cause 已定位在 <文件:行>（若已知，否则写"自行定位"）。
  Add a regression test that fails before the fix and passes after.
checks:
  - {name: regression-test, run: "uv run pytest -q tests/test_x.py::test_<名字>", expect: {exit: 0}}
  - {name: full-suite, run: "uv run pytest -q", expect: {exit: 0, stdout_not_contains: failed}}
```
**要点**：check 里点名那条回归测试 → Agent 必须真的加它。

### 3.3 写测试

```yaml
goal: Raise coverage of <模块> to <N>%. Cover: <枚举要覆盖的分支>.
checks:
  - {name: coverage, run: 'python -c "import subprocess,sys; o=subprocess.run([...],capture_output=True,text=True).stdout; sys.exit(0 if int(o.split(...))>=N else 1)"', expect: {exit: 0}}
```
**要点**：覆盖率门槛要有数字；"提高覆盖率"不是可断言目标。

### 3.4 改文档

```yaml
goal: |
  <文件> 的 <章节> 需要 <具体变更>。保持其他章节不变。
checks:
  - {name: file-exists, run: 'python -c "import pathlib,sys; sys.exit(0 if pathlib.Path(\"<文件>\").is_file() else 1)"', expect: {exit: 0}}
  - {name: section-present, run: 'python -c "import pathlib,sys; t=pathlib.Path(\"<文件>\").read_text(encoding=\"utf-8\"); sys.exit(0 if \"## <章节名>\" in t else 1)"', expect: {exit: 0}}
  - {name: links-valid, run: 'python -c "...re.findall(links)...", expect: {exit: 0}}
  - {name: git-diff-check, run: "git diff --check", expect: {exit: 0}}
```
**要点**：文档类只能断言"存在性、结构、链接、字数"——**内容正确性机器判不了，必须人读**。这是文档任务的天花板，写 YAML 时要承认它。

### 3.5 数据处理 / 迁移

```yaml
goal: 把 <输入> 按 <规则> 转成 <输出>。<幂等>：重复执行结果不变。
checks:
  - {name: output-exists, ...}
  - {name: row-count, run: 'python -c "...len(rows)==N..."', expect: {exit: 0}}
  - {name: idempotent, run: "python -c \"...跑两次结果相同...\"", expect: {exit: 0}}
  - {name: no-partial, run: 'python -c "...失败时不留半成品文件..."', expect: {exit: 0}}
```
**要点**：数据类任务必须验**幂等**与**不留半成品**，这两条最能抓出隐患。

---

## 4. 反模式（写 YAML 时逐条对照）

| 反模式 | 为什么坏 | 改成 |
|---|---|---|
| `goal: 优化/完善/改进 XXX` | 不可断言，Agent 会自由发挥 | 写成"改哪里、改成什么、怎么验" |
| `checks: [{run: "echo done"}]` | 永远通过，等于没验收 | 断言真实产物 |
| 检查里写 `test -f` / `cat` / `grep` | Windows 上不存在 | 用 `python -c` |
| 检查命令会写文件 | 污染 diff，可能触发只读校验失败 | 只读检查 |
| 检查依赖网络 | 沙箱/代理不稳定 → 假失败 | 用本地断言，或标记为 `optional` |
| constraints 与 checks 互斥 | 任务死锁，3 轮全 FAILED | 写完逐条对照有没有解 |
| `max_rounds: 1` + 严苛检查 | 一次不完美就 FAILED | 给 2–3 轮 |
| 把凭证写进 goal | 泄露且不可执行 | 写进 constraints 让人代跑 |
| goal 说"保留原内容"但没写进 constraints | Agent 会顺手重写 | 两边都写 |
| 一个任务塞 5 个无关目标 | 验收标准互相干扰，难收敛 | 拆成多个 task |

---

## 5. 敏感任务与目录约定

- `tasks/` 里的东西**会进公开仓库**；含公司名/内部链接/个人数据的任务放**仓库外**（如 `D:/Projects/ag-tasks/`）
- `.gitignore` 已拦 `tasks.local/` 与 `*.local.task.yaml` 作为双保险
- `runs/` 已 gitignore：执行日志、diff、verdict 都留在本地
- 详见 `tasks/README.md`

---

## 6. 写完后的九问自检

1. `goal` 里每个数字/文件名，`checks` 里都有一条对应断言吗？
2. 执行方看不见 `expect`——"必须产出什么"写进 goal 或藏在 `run` 命令里了吗？
3. 是否**存在**满足 constraints + checks 的解？（逐条对照）
4. 检查命令都是只读、确定、快速、跨平台（`python -c`）的吗？
5. 执行与验收是不同模型族吗？
6. Windows 上写了 `sandbox: danger-full-access` 吗？
7. 目标仓库工作树干净吗？有未提交改动吗？
8. `max_rounds` 给了几轮？最坏情况成本能接受吗？
9. 这些 `checks` 里有没有一条是"我本以为机器能验、但其实只有人看得出来"的？把它移到人工检查清单。

---

## 7. 失败诊断速查

| 现象 | 含义 | 处理 |
|---|---|---|
| `BLOCKED: no changes detected` | 执行方跑完但工作树零改动 | 看 `runs/<id>/round-1/executor.log`；常见原因：沙箱起不了进程、goal 太模糊、已存在同名文件 |
| `BLOCKED: UNKNOWN: FileNotFoundError [WinError 2]` | 可执行文件找不到（`.cmd` 未解析） | 确认用的是当前版本（已修） |
| `BLOCKED: verifier exited 1: 输入行太长` | prompt 超 Windows argv 上限 | 已修（改走 stdin） |
| `FAILED` + 同一 fingerprint | 同一缺陷连续两轮未消除 | 改 goal/checks 或 constraints，而不是加轮次 |
| `FAILED` + `max_rounds exhausted` | 轮次用完仍不过 | 看最后一轮 defects，多半是 goal 与 checks 不一致 |
| `SpecError: repo points to AgentGate itself` | 忘了 `--allow-self` | 加参数 |
| `ModelPairError: 同一模型族` | 执行与验收同族 | 换非 DeepSeek 验收模型 |
| `Run already exists` | 同 id 已有 run | 合并后 `ag clean <id>`，或换 id |
