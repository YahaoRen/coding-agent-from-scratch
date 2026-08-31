# One-minute English Introduction

下面这段使用简单句，正常语速约 45–55 秒。面试前多读几遍，不要额外加入姓名或学校。

> Hello. This project is a lightweight coding agent built from scratch in Python. It does not use any agent framework or agent SDK.
>
> A user gives a coding task, and the model can inspect files, search code, edit files, and run tests through a small set of local tools. Each tool result goes back to the model, and this process repeats until the task is finished.
>
> For safety, file access stays inside one workspace. Changes and commands require approval, and the agent limits repeated actions and large outputs.
>
> The project also supports retries, context control, secret redaction, and offline end-to-end testing.

## 中文意思

这是一个用 Python 从零实现的轻量编程智能体，不使用 Agent 框架。用户给出任务后，模型能通过本地工具查看、搜索和修改文件，并运行测试。每次工具结果都会返回模型，直到任务完成。项目还用工作区边界、人工审批、重复操作限制和输出限制保护本地环境，并支持重试、上下文控制、密钥脱敏和离线端到端测试。
