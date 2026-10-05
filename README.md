# agent

Glocal AI 原生办公体系的任务工作台与跨应用协调层。目标入口是
`oa.glocalstorage.com`；OA 管办公单据与流程，Aimail 管邮件，agent 连接各应用，
在授予的权限内阅读、分析、规划和调用工具。当前版本是独立的**本机文件只读试点**，
尚未部署到 OA，也没有跨应用业务写入。

## 当前可用范围

- 在 M5 上明确授权一个目录，默认 `~/Downloads`。点击扫描后才列出文件；
  默认不递归子目录，勾选后才包含子目录。模型只接收用户选中的文件文字。
- 预览 PDF 文字层、DOCX、XLSX/XLSM、PPTX、TXT/MD/CSV/TSV，记录原件
  SHA-256、页码/段落/单元格出处、解析器和读取限制。
- Spark Ollama 原生 API、LiteLLM/OpenAI 兼容 API、Codex CLI、Claude Code CLI
  共用结构化分析与来源核对。后端由操作员在启动前配置，当前不在运行中切换。
- 归纳资料类型、生成候选事实、引用和行动建议。逐字检查引用是否命中已读取的
  原文块；命中不证明模型解释或业务结论正确。结果不会写入订单、合同或客户主档。
- 导出 Word 报告、候选事实 Excel、Markdown 和完整 JSON，保存任务历史。
  原件不重命名、不移动、不覆盖、不删除。

图片和扫描 PDF 明确标记需要 OCR；当前未接入视觉模型。Excel 公式只展示，
不执行宏、不重算公式、不保证缓存数值最新。PPTX 目前读取幻灯片文字，未提供
原生 PPT 编辑/生成。Word 与 Excel 导出中的不可打印字符转为替代符，精确引用
保存在 JSON。长文件与批量上下文只读取部分，界面和报告会说明。

## M5 启动

源码路径遵循 `~/code/agent`，需要 `uv` 和 Python 3.12；`uv sync` 按锁文件安装。
以下命令也可在 M3/M4 的 macOS 本机使用，前提是各机自行配置文件授权与模型网络。
不会通过硬编码机器用户名访问其他 Mac。

**[M5 本机，已检出本仓库]**

```bash
bash ~/code/agent/tools/start_local.sh
```

首次运行会询问模型后端、模型名、API 地址/专用 key 和授权目录。随后打开
<http://127.0.0.1:8768>。浏览器显示“已配置”只表示本机配置存在或 CLI 可执行文件
已找到；实际连接、登录与模型权限在任务调用时验证。

### 使用现有 ssh spark 连接

如果 Spark 的 Ollama 只监听它的 `127.0.0.1:11434`，用现有 SSH 别名建立隧道。
这仍然是工作台通过 HTTP API 调用 Spark，SSH 只负责网络传输，不逐次运行模型命令。

**[M5 本机，另一个终端，保持打开]**

```bash
bash ~/code/agent/tools/connect_spark.sh
```

工作台配置选择 Spark Ollama API，地址输入 `http://127.0.0.1:11435`。
默认模型名采用 infra 当前登记的 `qwen3.8:27b`，须以 Spark 实际模型标签核对。
若已有可访问的 Spark API 或网关，直接输入那个服务地址；无需隧道。
不要把 Spark 端的 `127.0.0.1:11434` 当作 M5 自己的 Ollama 地址。

使用 LiteLLM 时，模型填写网关路由（例如 `brain`）；使用原生 Ollama 时填写实际 tag。
agent 使用自己的 key 和模型权限，**不会读取 Aimail、infra 或其他应用的私有配置**。
Aimail 的 key 可能只有 `fast` 权限，不能假定它可以调用 `brain`。

### Codex / Claude Code CLI

配置中选择相应 CLI，并输入明确模型名。CLI 必须在启动 agent 的同一台 Mac
安装且已登录。适配逻辑参考 Aimail 的文本推理后端；使用独立临时目录、stdin、
固定 argv，禁用文件工具、shell、MCP 和 hooks，输出经过同一 schema 与引用核对。
支持这些参数的 CLI 版本必须在本机验证；参数不支持会失败，不降低隔离条件。
CLI 线路会将所选文字交给相应云端模型，Spark API 线路使用你配置的内网服务。

**[M5 本机，重新配置后重启工作台]**

```bash
cd ~/code/agent && uv run glocal-agent configure
```

可选 `CODEX_CLI_COMMAND` / `CLAUDE_CODE_CLI_COMMAND` 是一个可执行文件路径，
不能包含命令参数。CLI 登录文件由 CLI 本身使用，agent 不读取或复制登录凭据。

## 运行数据

- 配置：`~/.config/agent/config.json`，权限 `0600`；不进入 Git。
- 任务、提取文字快照和报告：`~/.local/share/agent/`，本机私人数据。
- 输入目录与输出目录分开。源文件只读，报告生成到运行目录；报告下载另存由浏览器处理。
- 同一运行目录只允许一个实例。每次一个模型任务，减少 Spark 与 CLI 的资源争用。
- 重启后的未完成任务标记为中断，保留记录；用户核对后重新提交，不自动重复调用。
- 当前没有自动清理或备份，请把运行目录纳入本机备份与保留策略。

仅使用本项目 `AGENT_*` 环境变量或配置文件。支持 `AGENT_PROVIDER`、
`AGENT_BASE_URL`、`AGENT_MODEL`、`AGENT_API_KEY`、`AGENT_ROOT`、`AGENT_DATA_DIR`、
`AGENT_CONFIG`。界面和错误不会展示 API key、模型 stderr 或私有配置。

目录扫描最多 2000 文件，单文件最多 15 MiB，解析最多 50 页/16000 字符，表格有
行数/单元格和 Office ZIP 展开限制。单次最多 6 份材料，模型原文总预算 24000 字符，
每文件最多 8000 字符；达到限制明确标记部分读取。当前不构成完整合同审阅能力。

## 开发与验证

**[当前云端开发环境 /workspace/agent]**

```bash
uv sync --locked --group dev
uv run ruff check src tests
uv run pytest
```

测试使用合成材料、模拟 API 和假 CLI，不发送真实文件，也不能证明 M5、Spark、
真实 CLI 或 OA 生产环境已经连通。界面通过 localhost 提供，不能直接公网部署。
有关跨应用边界和下一阶段，见 [架构](docs/architecture.md)。
