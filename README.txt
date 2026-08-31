Coding Agent from Scratch

仓库地址
https://github.com/YahaoRen/coding-agent-from-scratch

项目简介
这是一个用 Python 从零实现的轻量编程智能体，不依赖任何 Agent 框架或 Agent SDK。用户给出任务后，模型可以查看文件、搜索代码、修改文件并运行测试，再根据结果继续工作，直到完成任务或达到安全上限。

运行方法
要求 Python 3.10 或更高版本。复制 .env.example 为 .env，填写 API Key、兼容接口的 /v1 地址和模型名称。建议把 .env 放在目标工作区之外。

检查配置：
python -m coding_agent doctor

运行演示：
python -m coding_agent run "修复当前项目中的失败测试，不要修改测试，最后确认全部测试通过" --workspace examples/inventory_reservation --env-file .env

运行项目测试：
python -m unittest discover -s tests -v

特色功能
1. 自行实现模型通信、工具协议、Agent 循环、上下文管理、结束判断和错误处理。
2. 提供受限的文件浏览、文本搜索、精确编辑、新建文件和命令执行工具。
3. 文件路径限制在指定工作区，敏感文件不可读取；修改和命令默认需要人工确认。
4. 限制模型轮数、重复调用、命令时间及输出大小，并对已知密钥脱敏。
5. 支持临时错误重试、上下文裁剪、脱敏会话记录和完全离线的端到端测试。

说明
命令审批不是操作系统级沙箱；自动批准所有操作只适用于可信且隔离的工作区。真实模型的效果取决于所选模型及其工具调用能力。
