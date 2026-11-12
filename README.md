# Serial Hub MCP

Windows 上的共享串口网关：**一个常驻进程占用物理 COM，多个 OpenCode 会话通过 MCP 使用，Xshell 通过本机 Telnet 同时观察/接管。** 不管理板子、不编译固件、不自动刷机。

> 初始版本，尚非无人值守实验室控制系统。只有同一串口的观察可以并发；输入是同一个终端，通过控制权和事务排队协调，不是多个独立 shell。

```text
OpenCode A ─ stdio MCP adapter ─┐
OpenCode B ─ stdio MCP adapter ─┼─ loopback HTTP + token ─ daemon ─ COM3 / COM8 / ...
Xshell ─── authenticated loopback Telnet ──────────────────┘
```

## 安装（Windows / PowerShell）

需要 Python 3.11+、Git。无须管理员权限、虚拟串口驱动或板端网络服务。

```powershell
git clone https://github.com/dongqifan/serial-hub_mcp.git
Set-Location serial-hub_mcp
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

也可以手动安装：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m pytest -q
```

服务、MCP 适配器必须使用同一 Windows 用户和相同的 `SERIAL_HUB_HOME` / `SERIAL_HUB_API_PORT` 配置。

## 启动共享服务

先断开 Xshell 等软件对物理 COM 的直接连接。无需退出 Xshell，只需要断开对应 SERIAL 标签。

```powershell
# 不主动打开任何串口；MCP open_port 时按需打开
.\.venv\Scripts\serial-hub.exe serve

# 或显式打开已确认参数的串口，持续抓取日志
.\.venv\Scripts\serial-hub.exe serve --port COM3 --baudrate 115200

# 另一个终端查看状态
.\.venv\Scripts\serial-hub.exe status
```

默认串口参数是 115200 / 8N1 / 无流控，DTR/RTS 预设为低电平。其他参数通过 MCP `open_port` 传入。**驱动/硬件仍可能在打开端口时产生控制线毛刺；这不是硬件级无复位保证。** 已打开端口的参数不一致时拒绝复用，不悄悄改参数。

同一时间只启动一个 daemon（默认 API 端口 8766）。关闭 MCP 会话不会关闭 daemon，也不会停止 RX 抓取。服务退出会关闭串口。

## OpenCode 全局接入（V2）

把下列项**合并**到 `~/.config/opencode/opencode.jsonc`，保留已有设置；路径替换为真实安装路径：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "serial_hub": {
        "type": "local",
        "command": ["D:/tools/serial-hub_mcp/.venv/Scripts/serial-hub-mcp.exe"],
        "cwd": "D:/tools/serial-hub_mcp"
      }
    }
  }
}
```

每个适配器只连接 daemon，不占用 COM，不需要 MCP 连接池。配置自动加载；必要时 `/reload`，在 `/mcps` 检查连接。

OpenCode 的 `_meta["ai.opencode/sessionID"]` 用作控制权关联值；没有该字段的其他 MCP 客户端退化为每个适配器进程一个身份。身份不是认证，连接由本机令牌保护。

示例对话：

```text
列出串口，打开 COM3，115200 8N1 无流控，先只读取输出。
在 COM3 执行 pwd，使用刚观察到的完整 shell 提示符作为结束标志。
读取 COM8 最近输出，不发送任何字符。
```

### 工具

| 工具 | 用途 |
|---|---|
| `list_ports` | 实时枚举 Windows 串口及已共享连接 |
| `open_port` | 打开/复用端口；返回 Telnet 入口和日志位置 |
| `port_status` | 连接状态、控制者、租约、游标、旁观者数量 |
| `read_output` | 按独立游标读取 RX；含 lossless base64、溢出数量 |
| `execute` | 完整事务排队，发送文本和换行，等待字面量结束标志 |
| `acquire_control` | 申请/续期控制权；可等待，不可强抢人工 |
| `send` / `send_bytes` | 持有控制权时发送精确文本/字节，不自动换行 |
| `release_control` | 释放控制权；默认不确认终端空闲 |
| `acknowledge_state` | 根据实际输出明确确认终端空闲，不发送字符 |
| `close_port` | 显式关闭端口；拒绝其他控制者、Telnet 连接、排队/运行任务 |

`execute` 的 `expect` 是**字面量**，不是正则或退出码。用真实、足够具体的提示符，不能拿命令本身/回显当完成标志。匹配只说明收到了指定文本，不证明命令成功、终端一定空闲。无参数推断/自动识别 shell 的魔法。

`read_output` 无游标时读取保留缓冲，使用返回的 `cursor` 可增量读取。各调用者自行保存游标，不会互相消费输出。原始字节可能在文本编码边界切开；需要严格还原时使用 base64 和增量解码。关闭重开后旧游标失效。RX 内存每端口 1 MiB；单次读取最多 64 KiB；溢出显式返回 `bytes_lost`。MCP 命令结果仅保留最多 64 KiB 尾部，完整 RX 记录在本机轮转日志。

## Xshell

1. 用 MCP 打开端口，或服务启动时指定 `--port COM3`。
2. Xshell 新建 **TELNET** 会话，主机 `127.0.0.1`，端口查看 `open_port` 或 `serial-hub status` 返回值。
3. 默认尝试 `2300 + COM号`，例如 COM3 → 2303，COM8 → 2308；被占用或超出端口范围则分配其他本机端口，**以返回值为准**。
4. 关闭本地回显，不使用本地行编辑/自动登录脚本；字符编码与 daemon 的 `--encoding` 一致（默认 UTF-8）。
5. 在 `Access token:` 提示处粘贴本机令牌文件内容并回车。令牌位置通过以下命令查看：

```powershell
.\.venv\Scripts\serial-hub.exe token-path
# 仅在本机查看，不要复制到 GitHub 或聊天记录
Get-Content "$env:LOCALAPPDATA\serial-hub-mcp\access.token"
```

默认 WATCH 模式，普通按键不会发送到串口。管理操作先按 **Ctrl+]**，再按一个键（不是一起按两个字母）：

| 后续键 | 操作 |
|---|---|
| `a` | 申请控制权，其他控制者存在时拒绝 |
| `f` | 人工强制接管，AI 当前事务会标记中断，不自动重发 |
| `i` | 看过输出后，明确确认终端已空闲 |
| `s` | 释放控制权，转 WATCH；不自动确认空闲 |
| `w` | 显示控制者及状态 |
| `?` | 帮助 |
| `q` | 断开当前 Telnet 客户端，不关闭串口 |
| 再按 Ctrl+] | 发送字面量 Ctrl+]（需要控制权） |

人工交互每次写入续期，空闲租约一小时。人工释放前若已经回到 shell 提示符，先 **Ctrl+] i** 再 **Ctrl+] s**，AI 即可继续；否则需要后续控制者检查日志并确认状态。

## 并发、安全和故障语义

- 不同 COM 独立运行；同一 COM 的 `execute` 完整事务 FIFO 排队。交互会话可持有租约，多步操作不被其他 AI 插入。
- 普通输入需要租约，读日志不需要。人工强制接管会使旧事务失效；不得把中断结果当成功。
- 超时、取消、租约过期、串口断线不会自动发送 Ctrl+C、回车或重试。发送后未确认结束则标记 `needs_confirmation`，阻止下一次盲目执行。
- v0.1 **断线后不自动重连**。保留状态/日志，确认其他使用者已结束后显式 close/open；不把原任务迁移到其他 COM。这是有意保守的首版行为。
- 所有 MCP 会话都可能被动读取，不是持久订阅；`close_port` 无法知道一个不活跃会话是否仍需要后续抓取。关闭前必须征求相关使用者同意。
- 单次串口写入同步且限时，避免人工接管后后台线程继续写旧输入；代价是驱动写阻塞时 daemon 事件循环最多可能暂停约 1 秒。高波特率/恶劣驱动下需实际验证。
- API 和 Telnet 只监听 IPv4 loopback，HTTP 检查令牌/Host/Origin；Telnet 也要求令牌。**不是多用户安全隔离系统**：同一 Windows 用户下能读取令牌的进程属于可信边界。
- Telnet 是明文协议，不要做公网转发，也不要用浏览器网页代理暴露它；需要团队远程使用时另行设计 SSH 隧道及身份权限。
- 日志包含 RX 和 TX 原始 base64，**不是脱敏日志**，可含密码、网络参数和客户数据。默认每端口 8 MiB × 5 份，最多 32 个打开端口。日志轮转会删除最早记录，重要案例应另行安全归档。
- 工具不识别命令危险程度，不因获得串口控制权而获得重启、擦除或刷机授权。AI 操作权限仍由用户和 MCP 客户端控制。

## 文件位置

默认 `%LOCALAPPDATA%\serial-hub-mcp\`：

```text
access.token         本机认证令牌（不上传）
logs/COM3.jsonl      串口 RX/TX/控制事件（不上传）
logs/COM3.jsonl.1    轮转日志
```

环境变量：

- `SERIAL_HUB_HOME`：本机状态目录，daemon / MCP 必须一致。
- `SERIAL_HUB_API_PORT`：loopback API 端口，默认 8766，daemon / MCP 必须一致。

源码目录只存程序、测试、安装脚本，不存真实串口日志或令牌。卸载前停止 daemon 和自动启动任务；删除工具不自动删除日志，避免误删工作资料。

## 自动启动（可选）

先手动验证，再执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\register-startup.ps1
```

注册**当前用户登录时**启动的 Windows 任务 `SerialHubMCP`，默认不打开任何串口，MCP 按需打开。不是开机未登录服务；任务不请求管理员权限。移除：

```powershell
Unregister-ScheduledTask -TaskName SerialHubMCP -Confirm:$false
```

当前会话也可手动启动。不要同时运行任务和手动 daemon。使用 `Get-ScheduledTask -TaskName SerialHubMCP` 检查任务状态。

## 开发和测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
```

默认测试用内存模拟串口，不访问 COM3/COM8，不需要硬件。测试涵盖独立读取、缓冲覆盖、事务排队、超时、取消、人工接管、多端口、鉴权、Telnet 协议交互和 MCP schema。真实硬件和真实 Xshell 的验证范围单独记录在 [docs/VALIDATION.md](docs/VALIDATION.md)，不能把模拟测试当真机测试。

## License

MIT. 第三方依赖保留各自许可证。
