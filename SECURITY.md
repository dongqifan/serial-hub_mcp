# Security and privacy

This is a local developer tool, not a remote multi-tenant console server.

- Never bind/forward HTTP or Telnet listeners to an untrusted network.
- Treat all processes able to read the current user's token as trusted. Session IDs are correlation values, not credentials.
- Protect `%LOCALAPPDATA%/serial-hub-mcp` with Windows account permissions. The default inherits the user's profile ACLs; no additional multi-user sandbox is claimed.
- RX/TX JSONL logs contain raw base64 payloads, including passwords and customer information. Base64 is not encryption. Do not attach logs, token files, configuration secrets or device output to public issues.
- Tool access permits arbitrary serial input. Do not grant autonomous destructive hardware operations merely because the tool is installed.
- Open/close may affect control lines depending on hardware and drivers; test reset-sensitive hardware separately.
- Default limits: 32 open ports, 16 authenticated viewers per port, 128 active Telnet shells, 30-second authentication deadline, bounded RX and per-port log rotation. These are not a comprehensive defense against malicious local processes.

For a suspected vulnerability, contact the maintainer privately through GitHub rather than posting credentials or an exploitable installation's details. Report the affected version, a synthetic reproduction and expected behavior; do not include real device logs.
