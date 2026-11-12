# Serial Hub MCP

Windows 共享串口网关：**一个常驻进程连接物理 COM，多个 OpenCode/MCP 会话和 Xshell Telnet 客户端可以同时输入、同时看输出。** 不管理板子、不编译固件、不自动刷机。

**v0.2 起默认自由并发读写。没有控制权、租约、接管按键、只读旁观模式，也不等待前一条命令完成后才允许下一次输入。** v0.1 的轮流控制设计已废弃。

**v0.3 起完全取消认证。Xshell Telnet 连接即用，MCP/CLI 不读取、不创建令牌文件；两个入口仍只监听 `127.0.0.1`。默认信任本机环境。**

```text
OpenCode A ─ stdio MCP adapter ─┐
OpenCode B ─ stdio MCP adapter ─┼─ loopback HTTP ─ daemon ─ COM3 / COM8 / ...
Xshell 1 ─ loopback Telnet ─────┤
Xshell 2 ─ loopback Telnet ─────┘
```

> 同一串口仍只有一个物理字节流、一个板端终端，不是多个独立 shell。多个客户端的按键/命令可以混在一起，Ctrl+C 会影响共同终端，输出向所有客户端广播。工具不擅自禁止并发，但不能保证各命令输出独立、提示符匹配属于当前调用，或多人同时输入时命令仍然有效。

## 安装（Windows / PowerShell）

需要 Python 3.11+、Git。无须管理员权限、虚拟串口驱动或板端网络服务。

```powershell
git clone https://github.com/dongqifan/serial-hub_mcp.git
Set-Location serial-hub_mcp
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

手动安装与测试：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m pytest -q
```

服务、MCP 适配器必须使用相同的 `SERIAL_HUB_API_PORT`。`SERIAL_HUB_HOME` 只决定服务的日志/诊断目录，不再用来共享认证信息。

## 启动共享服务

断开其他软件对真实 COM 的直接连接；Xshell 改接本工具提供的 Telnet。

```powershell
# 按需打开：MCP open_port 时开始接收日志
.\.venv\Scripts\serial-hub.exe serve

# 或启动时打开指定串口，可重复 --port
.\.venv\Scripts\serial-hub.exe serve --port COM3 --baudrate 115200

# 另一个终端查看状态和 Telnet 入口
.\.venv\Scripts\serial-hub.exe status
```

默认 115200 / 8N1 / 无流控，DTR/RTS 预设低电平；其他参数通过 MCP `open_port` 传入。**驱动/硬件仍可能在打开时产生控制线毛刺，不能保证不复位。** 参数不同的重复打开会拒绝，不会悄悄改现有连接。

只启动一个 daemon（默认 API 端口 8766）。关闭 MCP 会话不关闭 daemon，服务退出才关闭物理串口。首次打开前的日志无法补抓。

## OpenCode 全局接入（V2）

将下列内容合并到 `~/.config/opencode/opencode.jsonc`，替换安装路径并保留已有设置：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "serial_hub": {
        "type": "local",
        "command": ["D:/tools/serial-hub_mcp/.venv/Scripts/python.exe", "-m", "serial_hub_mcp.mcp_server"],
        "cwd": "D:/tools/serial-hub_mcp"
      }
    }
  }
}
```

每个适配器只连接 daemon，不占用 COM。配置自动加载；必要时 `/reload`，通过 `/mcps` 检查。

OpenCode 的 `_meta["ai.opencode/sessionID"]` 只标注 TX 来源，**不用于认证、获取控制权或限制其他输入**。其他 MCP 客户端使用每个适配器进程一个来源标识。本机连接无需任何凭据。

```text
使用 COM3，115200 8N1 无流控，先读取输出。
向 COM3 直接发送 pwd 和回车。
在 COM3 执行命令并等待指定文本；其他会话和 Xshell 仍可同时输入。
```

| 工具 | 用途 |
|---|---|
| `list_ports` | 实时枚举串口与打开的共享连接 |
| `open_port` | 打开/复用 COMx，开始持续 RX，返回 Telnet 入口 |
| `port_status` | 共享模式、活跃 execute 数量、写入序号、游标和连接状态 |
| `read_output` | 独立游标读取 RX，不消费别人的输出；含原始 base64 |
| `send` / `send_bytes` | 立即发送文本/原始字节，不自动换行，无须申请控制权 |
| `execute` | 立即发送文本及换行，再独立观察共享 RX 直到字面量匹配或超时 |
| `close_port` | 显式关闭；有 Telnet 连接或活跃 execute 时拒绝关闭 |

### execute 的共享输出语义

- 不获取控制权、不排队等其他命令结束。其他客户端可以在它等待输出时随时写入。
- 每个调用有独立 RX 游标，但看到的是共同输出，可能包含其他调用者的响应。
- `expect` 是字面量，不是正则/退出码；任何人的输出都可能触发匹配。**matched 不证明你的命令已完成或执行成功。**
- 返回 `shared_output=true`、`additional_writes_observed`（发送后、返回前观察到的其他写入次数）和警告。该计数不是输出归属证明，先前命令也可能还在输出。
- 超时不发送 Ctrl+C、不自动重试、不禁止后续输入。调用者自行判断终端状态；没有 acknowledge_state 步骤。
- 单次结果最多保留 64 KiB 尾部；完整 RX 记录在有界本机轮转日志。`wait_seconds` 仅为兼容 v0.1 保留，已忽略。

`read_output` 无游标时回放保留缓冲，使用返回的 `cursor` 可增量读取。每个人自行保存游标，互不消费。文本编码边界可能切开，严格还原请使用 base64 和增量解码。关闭重开后旧游标失效。RX 内存每端口 1 MiB，单次最多 64 KiB，`bytes_lost` 报告缓冲覆盖。

## Xshell：连接后即可输入

1. MCP 打开串口，或 daemon 启动时指定 `--port COM3`。
2. Xshell 新建 **TELNET** 会话，主机 `127.0.0.1`。端口以 `open_port` / `status` 返回值为准。
3. 默认尝试 `2300 + COM号`：COM3 → 2303、COM8 → 2308；占用时分配其他端口。
4. 字符编码与 daemon `--encoding` 一致，默认 UTF-8。关闭本地回显，使用普通远程终端输入。
5. **连接后直接收发，无用户名、密码或 Access token 提示。** 无需 Ctrl+] 接管，没有管理按键，Ctrl+] 也会直接转发给串口（客户端自己截获的快捷键除外）。板端操作系统自己的登录提示不在本工具控制范围内。

多个 Xshell 标签页、多个 AI 会话都可以直接写入。Telnet CR LF / CR NUL 转成一次串口回车，控制字符直接转发。粘贴与人工逐字输入的分块可能不同，多方输入可能相互穿插；不进行行缓冲或命令级调度。

关闭某个 Xshell 标签只断开那个连接，不关闭真实串口。

## 并发、故障与安全边界

- 每个收到的写入块通过一次有界同步串口 write 发送，块内不被其他 API 调用插字节。**只串行化物理写入这一瞬间，不锁住命令/响应周期**；硬件本来只能按顺序传输字节。
- 驱动写入超时 1 秒，可能使事件循环暂时阻塞；高负载需实际压力验证。
- 串口断线后不自动重连，也不重放命令；确认使用者结束后显式 close/open，不把 COM3 自动迁移到 COM8。
- 被动 MCP 读者不是持久订阅，关闭前需确认无人需要后续日志；端口关闭拒绝正在连接的 Telnet 和活跃 execute。
- 不自动发送回车、Ctrl+C、BREAK，也不自动复位、重试命令。用户通过 send/Xshell 发出的控制键照常转发，可能影响其他人的程序。
- API 和 Telnet 仅监听 IPv4 loopback，**均无认证，默认信任所有能访问这些本机端口的进程/用户**；HTTP 仍校验 Host/Origin。这不是多用户隔离系统，Host/Origin 校验也不是身份认证。
- Telnet 为明文协议，不要向公网/局域网转发；远程团队共享需另行设计身份认证和加密。
- 原始日志可含密码、客户数据。Base64 不是加密。默认每端口 8 MiB × 5 份，最多 32 个打开端口；日志有覆盖，重要资料自行安全归档。
- 工具不判断串口命令的危险程度，安装工具不等于授权 AI 刷机、重启、擦除或改配置。

## 文件位置和自动启动

默认 `%LOCALAPPDATA%\serial-hub-mcp\`：

```text
logs/COM3.jsonl       原始 RX/TX 与事件，不上传
logs/COM3.jsonl.1     轮转日志
daemon.stderr.log    启动诊断（pythonw）
```

- `SERIAL_HUB_HOME`：daemon 的日志/诊断目录，MCP 不依赖此目录。
- `SERIAL_HUB_API_PORT`：API 端口，默认 8766，两者必须一致。

验证手动启动后，可注册当前用户登录启动任务（不需要管理员权限）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\register-startup.ps1
# 先停止手动 daemon，再启动任务，勿同时运行两个
Start-ScheduledTask -TaskName SerialHubMCP
```

任务默认不打开串口，由 MCP 按需打开。它是登录启动，不是无人登录时的系统服务。移除任务：

```powershell
Unregister-ScheduledTask -TaskName SerialHubMCP -Confirm:$false
```

更新：停止 daemon，`git pull`，重新运行安装脚本，启动 daemon，再 `/reload` MCP 让工具说明更新。服务重启会暂时断开 Telnet，需重新连接；不会自动恢复打开的端口。v0.1 已运行适配器的三个旧控制 API 在 v0.2 服务端只返回兼容提示，不再限制输入；新适配器不暴露这些工具。

升级 v0.3 后，旧的 `access.token` 文件不再读取或校验，可保留，也可自行删除；程序不会自动删除旧文件。`token-path` 命令已移除。请重新加载旧 MCP 适配器以停止它读取旧令牌。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
```

默认测试全部使用模拟串口，不访问实际 COM。覆盖：多个 execute 在任何响应之前都已发出、等待期间其他 MCP/Telnet 仍可写入、独立读取、超时不阻止输入、控制键透传、无令牌的 MCP/CLI/API/Telnet 连接、Host/Origin 边界、断线及日志。真实硬件范围记录在 [docs/VALIDATION.md](docs/VALIDATION.md)，模拟 Telnet 测试不等于真实 Xshell UI 验证。

MIT；第三方依赖保留各自许可证。
