# 两分钟演示视频脚本

目标：让评委在两分钟内清楚看到“初始测试失败 → 智能体分析并修改 → 测试通过”，同时简单说明实现原理和安全措施。

## 录制前准备

1. 提前完成真实模型联调，并至少重复成功三次。
2. 关闭聊天软件、邮件、浏览器账号页和系统通知。
3. 终端不要显示姓名、学校、完整用户目录或 API Key。
4. 可在 PowerShell 临时隐藏当前路径：

```powershell
function global:prompt { "PS> " }
Clear-Host
```

5. 确认 `.env` 位于智能体仓库根目录，而目标工作区是子目录 `examples/inventory_reservation`。录屏中不要打开 `.env`。
6. 重置演示项目：

```powershell
python examples\reset_inventory_demo.py
```

## 时间安排和讲解词

### 0–15 秒：说明项目

画面停留在 README 的标题或干净终端。

讲解：

> 这是一个用 Python 从零实现的轻量编程智能体，没有使用任何 Agent 框架。它可以让模型查看文件、修改代码、运行命令，并根据结果继续工作。

### 15–30 秒：展示真实问题

```powershell
Push-Location examples\inventory_reservation
python -m unittest discover -s tests -v
Pop-Location
```

讲解：

> 这个库存项目要求一次订单要么全部预留成功，要么库存完全不变。现在有一项测试失败，因为程序在发现后续商品不足前，已经扣除了前面的商品。

### 30–90 秒：运行智能体

```powershell
python -m coding_agent run "请修复当前项目中的失败测试。业务要求：如果任何商品库存不足，整个预留必须失败，任何库存都不能变化。不要修改测试或降低断言。请先检查项目并运行测试，只做必要修改，最后再次运行测试确认全部通过。" --workspace examples\inventory_reservation --env-file .env
```

出现审批时，先看清操作，再输入 `y`。模型等待时间可以按题目要求剪辑或加速。

讲解：

> 智能体先通过只读工具了解项目。修改文件和运行命令属于有副作用的操作，所以默认需要人工确认。工具结果会以结构化数据返回给模型，模型再决定下一步。

### 90–110 秒：展示结果

如果智能体已经运行了测试，保留“全部通过”的输出；也可以再快速人工确认：

```powershell
Push-Location examples\inventory_reservation
python -m unittest discover -s tests -v
Pop-Location
```

讲解：

> 修复后，两项测试都通过。智能体没有修改测试，而是把处理过程改成先检查全部库存，再统一扣减。

### 110–120 秒：一句话总结

短暂展示 `docs/architecture.png`。

讲解：

> 核心循环、工具执行、上下文管理和错误处理都由项目自行实现，并加入了路径限制、密钥保护和重复调用停止机制。

## 导出前检查

- 总时长不超过 2 分钟。
- MP4 格式，文件不超过 200 MB。
- 画面和声音中没有姓名、学校、账号头像、个人主页、通知或 API Key。
- 关键文字在普通笔记本屏幕上仍清晰可读。
- 从头播放一次，确认声音、画面和加速片段都正常。
