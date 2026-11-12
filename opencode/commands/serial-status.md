---
description: 查询指定 COM 详情；无参数时列出共享串口
subagent: false
---

仅查询串口共享状态，参数如下：

$ARGUMENTS

有合法 COM 名时调用 serial_hub MCP 的 port_status，无参数时调用 list_ports。其他参数请说明用法，不执行任意命令。
简要报告占用/连接状态、串口参数、Telnet 地址、Telnet 客户端数、活跃 execute 数量和日志路径（若有）。
服务未打开该 COM 时明确说明，不自动打开。MCP 被动读者不计入 Telnet 客户端数。
不发送串口文本，不关闭或重新启动服务。工具不可用时如实说明，不猜测结果。
