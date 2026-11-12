# Security and privacy

This is a local developer tool, not a remote multi-tenant console server.

- Never bind/forward HTTP or Telnet listeners to an untrusted network.
- HTTP and Telnet have no authentication. Trust all local processes/users that can connect to the loopback listeners. Session IDs are correlation values, not credentials. HTTP Host/Origin checks remain, but are not authentication or a local-user isolation boundary.
- Protect `%LOCALAPPDATA%/serial-hub-mcp` with Windows account permissions. The default inherits the user's profile ACLs; no additional multi-user sandbox is claimed.
- RX/TX JSONL logs contain raw base64 payloads, including passwords and customer information. Base64 is not encryption. Do not attach logs, configuration secrets or device output to public issues. Old access.token files from v0.1/v0.2 are unused in v0.3 and are not read, created or automatically deleted.
- Tool access permits arbitrary serial input. Do not grant autonomous destructive hardware operations merely because the tool is installed.
- All MCP/Telnet clients can write concurrently without ownership. Inputs may mix, control keys affect the shared terminal, and any client's output can satisfy another client's expect string. No command-response isolation or safe concurrent shell execution is claimed.
- Open/close may affect control lines depending on hardware and drivers; test reset-sensitive hardware separately.
- Default limits: 32 open ports, 16 terminal clients per port, 128 active Telnet shells, bounded RX and per-port log rotation. These are not a comprehensive defense against malicious local processes.

For a suspected vulnerability, contact the maintainer privately through GitHub rather than posting credentials or an exploitable installation's details. Report the affected version, a synthetic reproduction and expected behavior; do not include real device logs.
