# Coding Agent from Scratch

一个从零实现的轻量编程智能体。项目强调代码清晰、依赖少，并完整展示模型交互、工具执行、上下文管理和终止判断，而不是封装现有 Agent 产品或框架。

## 当前进度

当前已提供可运行的命令行入口、环境配置读取、不依赖第三方包的 OpenAI 兼容模型客户端、自建工具注册层，以及有明确终止条件的 Agent 主循环。后续功能会以小步、可验证的提交逐步加入。

## 设计原则

- 只使用 Python 标准库和普通模型 API 客户端，不使用 Agent 框架或 Agent SDK。
- 每个模块只负责一类功能，优先选择容易阅读和解释的实现。
- 每个功能里程碑都附带测试，已推送的 Git 历史不改写。
- API Key 只从环境变量或未跟踪的本地配置读取。
- 工具错误会转换成结构化结果交还模型，不因一次失败终止整个进程。
- 主循环限制模型轮数与工具调用总数，避免模型陷入无限执行。

## 运行

要求 Python 3.10 或更高版本。

```powershell
python -m coding_agent --help
python -m coding_agent --version
```

运行测试：

```powershell
python -m unittest discover -s tests -v
```

## 模型配置

复制 `.env.example` 为 `.env`，填写以下本地配置：

```text
CODING_AGENT_API_KEY=你的密钥
CODING_AGENT_BASE_URL=兼容接口的 /v1 地址
CODING_AGENT_MODEL=模型名称
```

`.env` 已被 Git 忽略，真实密钥不得写进其他代码、文档或提交历史。模型适配器使用普通的 `/chat/completions` 接口；工具定义和 Agent 循环均由本项目自行实现。

## 开发路线

详细的功能边界和提交顺序见 [`docs/ROADMAP.md`](docs/ROADMAP.md)。
