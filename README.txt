Coding Agent from Scratch

仓库地址
https://github.com/YahaoRen/coding-agent-from-scratch

项目简介
这是一个用 Python 从零实现的轻量编程智能体，不依赖任何 Agent 框架或 Agent SDK。用户给出任务后，模型可以查看和搜索文件、修改代码、运行测试，再根据结果继续工作，直到完成或安全停止。

运行方法
要求 Python 3.10 或更高版本。复制 .env.example 为 .env，填写 API Key、兼容接口的 /v1 地址和模型名称。建议把 .env 放在目标工作区之外。

检查配置：
python -m coding_agent doctor

启动本地浏览器任务台：
python -m coding_agent web --workspace examples/inventory_reservation --env-file .env

也可使用命令行：
python -m coding_agent run "修复当前项目中的失败测试，不要修改测试，最后确认全部测试通过" --workspace examples/inventory_reservation --env-file .env

运行项目测试：
python -m unittest discover -s tests -v

特色功能
1. 自行实现模型通信、工具协议、Agent 循环、上下文管理和结束判断。
2. 本地任务台清楚展示执行时间线、文件差异、命令输出和运行结果，并支持停止任务。
3. 文件路径限制在指定工作区；只读操作自动执行，修改和命令逐项人工批准。
4. 限制模型轮数、重复调用、命令时间和输出大小；已知密钥会脱敏，且不会发送到网页。
5. CLI 与网页共用同一套核心，并提供完全离线的单元测试和端到端测试。

说明
命令审批不是操作系统级沙箱；获批命令仍可能访问工作区外资源。真实效果取决于所选模型及其工具调用能力。
