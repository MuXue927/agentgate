# STEP 1 报告：项目骨架 + `ag doctor`

状态：**PASS（第 1 步硬验收通过，未进入第 2 步）**

## 上一轮自述更正

- 上一轮说“已实现 `ag doctor`”不准确：代码存在，但 Typer 将唯一命令折叠到根，
  `ag doctor` 实际不可调用。
- 上一轮说“单元测试已写”不准确：测试文件存在，但当时没有 pytest 依赖，
  测试从未真实执行。
- 上一轮 `uv sync` 失败归因于代理不准确。后续确认是执行 Agent 的沙箱没有网络出口；
  主机侧依赖已由人安装完成。

本轮没有再次执行 `uv sync`、`uv add` 或任何依赖安装。

## 四项返工

### 1. 修复 `ag doctor` 路由

- 增加第二个命令 `ag version`，使 Typer 使用真正的命令组。
- 新增 `tests/test_cli.py`，覆盖 `ag --help` 列出 `doctor`，以及
  `ag doctor` 在必需探测通过时退出码为 0、输出包含 `backend`。
- 同时修正 CLI 在 GBK stdout 环境下输出中文提示的乱码问题。

### 2. DSH 改为可选探测并给出凭证建议

- DSH 不再经过 `.cmd` 包装，直接调用：
  `"D:/Software/DeepSeek Harness/DeepSeek Harness.exe" --expose-internals
  "D:/Software/DeepSeek Harness/resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js"`。
- 显式设置 `ELECTRON_RUN_AS_NODE=1`。
- DSH 从 `required` 降为 `optional`。
- 检测到 `MISSING_CREDENTIAL` 时输出：
  `DSH 验收方不可用：需设置 DEEPSEEK_API_KEY 环境变量（DSH GUI 里的 key 在 headless 下读不到）`。
- 增加测试锁定直接入口、环境变量和可选状态。
- 真实探测确认失败原因是缺少 `DEEPSEEK_API_KEY`，不是路径或 `.cmd` 空格。

### 3. 默认验收方改为 Codex 只读 + 非 DeepSeek 模型

- 默认验收方固定为：
  `codex exec --skip-git-repo-check -C <cwd> -s read-only -m nvidia/nemotron-3.5-lightning:free "reply OK"`。
- 探测表标签改为 `Codex (default verifier, read-only)`。
- 档位按模型差异判定：
  - 不同脚手架 + 不同模型：★★★；
  - 同脚手架 + 不同模型：★★；
  - 相同模型：不合格。
- 新增 `agentgate.validation.require_different_models()`，并在测试中确认相同模型抛错。
- 当前 `ag run` 属于第 2 步，尚不存在；第 2 步创建 `run` 时必须先调用该校验函数。

### 4. 更新根 `AGENTS.md`

- 铁律 4 改为“禁止执行与验收使用同一个模型”；不同模型 + `-s read-only` 合格。
- 新增“沙箱无网络”：新依赖不自行安装，必须在 STEP 报告里写
  “需要人代跑：`uv add <pkg>`”。
- 默认验收方和 DSH 可选凭证说明已同步。

## 改了哪些文件

- `AGENTS.md`
- `src/agentgate/__init__.py`
- `src/agentgate/__main__.py`
- `src/agentgate/cli.py`
- `src/agentgate/doctor.py`
- `src/agentgate/validation.py`
- `tests/test_cli.py`
- `tests/test_doctor.py`
- `STEP-1-报告.md`

依赖配置与 `uv.lock` 由人完成，本轮未改动。

## 验收命令与真实输出

### 1. `uv run pytest -q`

```text
........                                                               [100%]
8 passed, 2 subtests passed in 0.56s
```

### 2. `uv run ag --help`

```text
 Usage: ag [OPTIONS] COMMAND [ARGS]...

 Single-purpose Codex execution and read-only verification loop.

┌─ Options ───────────────────────────────────────────────────────────────────┐
│ --help          Show this message and exit.                                 │
└─────────────────────────────────────────────────────────────────────────────┘
┌─ Commands ──────────────────────────────────────────────────────────────────┐
│ doctor   Probe execution and verification backends.                         │
│ version  Print the AgentGate version.                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3. `uv run ag doctor`

```text
backend                             | role     | command                                                                                                                                                                                                       | result | time  
------------------------------------+----------+---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+--------+-------
Codex (executor)                    | required | codex exec --skip-git-repo-check -C D:\Projects\agentgate -m deepseek-v4-flash "reply OK"                                                                                                                     | PASS   | 5.12s 
Codex (default verifier, read-only) | required | codex exec --skip-git-repo-check -C D:\Projects\agentgate -s read-only -m nvidia/nemotron-3.5-lightning:free "reply OK"                                                                                       | PASS   | 10.61s
DSH (optional verifier)             | optional | "D:\Software\DeepSeek Harness\DeepSeek Harness.exe" --expose-internals "D:\Software\DeepSeek Harness\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js" headless "reply OK" --json | FAIL   | 4.39s 
Pi (optional verifier)              | optional | pi -p --no-tools --no-skills --no-session --model bigmodel/glm-4.7-flash --mode text "reply OK"                                                                                                               | FAIL   | 14.94s

Probe failures (raw output):
[DSH (optional verifier)] exit=1
stdout:
{"type":"session","sessionId":"session-b29df3ab-b90b-4301-a0d1-977668dbde4a","cwd":"D:\\Projects\\agentgate"}
{"type":"status","phase":"turn_start","turn":1}
{"type":"status","phase":"step_start","turn":1,"step":1}
{"type":"status","phase":"step_end","turn":1,"step":1}
{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"error","error":{"message":"llm-deepseek: no API key for provider route \"deepseek-official\"; store DEEPSEEK_API_KEY through the credentials service (the web Models page writes it), or export DEEPSEEK_API_KEY in the launching environment","code":"MISSING_CREDENTIAL"}}}
{"type":"final","text":""}
stderr:
dsh: all_proxy names a SOCKS proxy, which is not supported; connecting directly for that scheme — set an http:// or https:// proxy URL instead
dsh: MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official"; store DEEPSEEK_API_KEY through the credentials service (the web Models page writes it), or export DEEPSEEK_API_KEY in the launching environment
[Pi (optional verifier)] exit=1
stdout:
<empty>
stderr:
Connection error.

Repair commands / notes:
- DSH 验收方不可用：需设置 DEEPSEEK_API_KEY 环境变量（DSH GUI 里的 key 在 headless 下读不到）
- DSH 直接调用命令：$env:ELECTRON_RUN_AS_NODE="1"; & "D:\Software\DeepSeek Harness\DeepSeek Harness.exe" --expose-internals "D:\Software\DeepSeek Harness\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js" headless "reply OK" --json
- Pi 验收方不可用：需配置智谱 key（ZHIPU_API_KEY），再重开终端并运行 `ag doctor`。

Double-blind tier: ★★
```

### 4. `uv run ag doctor` 并回显退出码

当前工具 shell 是 PowerShell，因此使用等价写法：

```powershell
uv run ag doctor; $exitCode=$LASTEXITCODE; Write-Output "exit=$exitCode"
```

真实结果中的状态行：

```text
Codex (executor)                    | required | ... | PASS   | 4.94s
Codex (default verifier, read-only) | required | ... | PASS   | 7.11s
DSH (optional verifier)             | optional | ... | FAIL   | 3.28s
Pi (optional verifier)              | optional | ... | FAIL   | 14.92s
...
Double-blind tier: ★★
exit=0
```

必需项全部通过，因此 `exit=0`。

## 第 1 步结论

- 第 1 步硬验收：**PASS**。
- `ag doctor` 已可调用，帮助页能列出子命令。
- 必需探测全部通过，当前档位为 **★★**：
  Codex 执行 + Codex 只读验收，模型不同、脚手架相同。
- DSH 与 Pi 是可选探测，失败不阻断 doctor；两者均有可执行修复建议。
- 没有新增联网依赖，也没有进入第 2 步。

## 未做项与边界

- `ag run` 命令尚不存在，这是第 2 步范围。相同模型拒绝逻辑已经实现并测试，
  但尚未接到不存在的 `run` 启动路径。
- 方案文档 §1.2 仍残留一句旧结论“v0.2 的现实默认档是 ★”，与同节已更新的
  ★★ 实测表矛盾。本次未修改方案文档。

## 需要人代跑的联网操作

本轮无。本轮没有新增依赖；后续若需要新依赖，只会在 STEP 报告中列出
`uv add <pkg>`，不会自行联网安装。
