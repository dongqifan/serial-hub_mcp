# Changelog

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
