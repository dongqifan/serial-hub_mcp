---
description: 列出串口及共享服务占用状态
subagent: false
---

调用 serial_hub MCP 的 list_ports 工具，列出系统可见串口和共享服务当前打开的串口。仅查询，不打开、关闭或发送串口数据。
用中文表格列出 COM、服务占用/断线状态、波特率、Telnet 地址、Telnet 客户端数、活跃 execute 数量。
明确“本服务未占用”不代表未被其他软件占用；MCP 被动读者不是持久连接，不计入 Telnet 客户端数。
如果 MCP 不可用或 daemon 未运行，如实说明，不自动启动/重启服务，不猜测占用状态。
