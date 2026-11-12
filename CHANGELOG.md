# Changelog

## 0.1.0

- Shared Windows COM daemon, authenticated loopback RPC and lightweight stdio MCP adapters.
- Session-aware control leases, FIFO command transactions and explicit recovery after uncertain operations.
- Authenticated Telnet viewers, human takeover, independent RX cursors and rotating raw-byte JSONL logs.
- PowerShell installer, current-user logon task helper, OpenCode V2 configuration guide and simulated I/O tests.
- Initial COM3 hardware validation with two MCP clients and a Telnet protocol client; actual Xshell UI and USB reconnect acceptance remain manual.

Known limits: explicit reopen after disconnect; no terminal prompt auto-detection; command matching is not exit-code validation; no hardware flow/reset guarantees; no multi-user remote access isolation. See README and validation record.
