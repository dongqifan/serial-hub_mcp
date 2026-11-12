# Changelog

## 0.4.0

- Added CLI open/occupy, close/release, ports (table or JSON), and status COMx commands to manage COM handles in the running daemon without stopping other ports.
- Open accepts full serial parameters; close retains existing refusal for connected Telnet clients or active execute calls. No forced interruption or command writes are introduced.
- Added readable daemon/port error reporting and clarified that not held by this hub does not mean free from other applications.

## 0.3.0

- Removed authentication completely from loopback HTTP RPC, Telnet, MCP adapters and CLI status. Telnet clients connect and type directly without a gateway login.
- Removed token creation/loading and the token-path command. Existing access.token files are unused and left untouched.
- Kept IPv4 loopback-only listeners and HTTP Host/Origin checks; all local clients are trusted by design.
- Corrected initial Telnet CR-padding state so a leading LF or NUL is not swallowed; retained shared concurrent input/output and control-character forwarding.

## 0.2.0

- Correct concurrency semantics: all MCP and Telnet clients can write immediately, including while other execute calls await output.
- Removed control leases, forced takeover keys, watch-mode input dropping, command-response queues and idle-confirmation gates.
- Execute uses independent cursors over shared RX and reports a warning and subsequent-write count; matching is not command-output isolation.
- Telnet forwards control characters directly and normalizes CR LF / CR NUL. Token authentication remains, but no input acquisition step follows it.
- Legacy v0.1 control RPCs are harmless compatibility no-ops; new MCP adapters expose only eight data/lifecycle tools.

## 0.1.0

- Shared Windows COM daemon, authenticated loopback RPC and lightweight stdio MCP adapters.
- Session-aware control leases, FIFO command transactions and explicit recovery after uncertain operations.
- Authenticated Telnet viewers, human takeover, independent RX cursors and rotating raw-byte JSONL logs.
- PowerShell installer, current-user logon task helper, OpenCode V2 configuration guide and simulated I/O tests.
- Initial COM3 hardware validation with two MCP clients and a Telnet protocol client; actual Xshell UI and USB reconnect acceptance remain manual.

Known limits: explicit reopen after disconnect; no terminal prompt auto-detection; command matching is not exit-code validation; no hardware flow/reset guarantees; no multi-user remote access isolation. See README and validation record.
