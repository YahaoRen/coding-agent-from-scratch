# Coding Agent from Scratch

一个从零实现的轻量编程智能体。项目强调代码清晰、依赖少，并完整展示模型交互、工具执行、上下文管理和终止判断，而不是封装现有 Agent 产品或框架。

## 当前进度

当前是可运行的项目骨架，提供命令行入口和基础测试。后续功能会以小步、可验证的提交逐步加入。

## 设计原则

- 只使用 Python 标准库和普通模型 API 客户端，不使用 Agent 框架或 Agent SDK。
- 每个模块只负责一类功能，优先选择容易阅读和解释的实现。
- 每个功能里程碑都附带测试，已推送的 Git 历史不改写。
- API Key 只从环境变量或未跟踪的本地配置读取。

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

## 开发路线

详细的功能边界和提交顺序见 [`docs/ROADMAP.md`](docs/ROADMAP.md)。
