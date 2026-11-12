---
description: 释放指定 COM，不停止共享服务，例如 /serial-release COM3
subagent: false
---

用户明确请求从共享服务释放以下 COM：

$ARGUMENTS

仅接受一个合法 COM 名；缺失、多个或含义不明时询问用户，不自行选择端口，不把参数当 shell 代码执行。
调用 serial_hub MCP 的 close_port，仅释放指定 COM，不停止 daemon，不影响其他 COM，不关闭其他软件或进程。
如果因为 Telnet 客户端或活跃 execute 被拒绝，报告原因；可查询 port_status 展示连接数，提示先断开/结束对应使用者。不得绕过保护、强制中断或自动重试。
成功后说明该 COM 已释放、对应 Telnet 入口关闭，服务仍运行；之后 MCP 仍可再次打开它，并非永久禁用。
如果 COM 未打开或服务不可用，如实报告，不声称已成功释放。
