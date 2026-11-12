# Validation record

No customer data, raw terminal output, serial logs or credentials belong here.

## v0.3.0 — no authentication

- Windows / Python 3.12.10: 29 automated tests passed. Coverage includes HTTP RPC without Authorization, retained Host/Origin rejection, real stdio MCP clients and CLI status with no token file (and with an invalid unused legacy file), two Telnet clients writing during an execute wait, and leading LF/NUL plus split CR padding.
- Installed v0.3.0 and restarted the existing current-user logon task. COM3 reopened at 115200 / 8N1 / no flow control, input_mode=shared, Telnet 127.0.0.1:2303.
- Real COM3 verification: unauthenticated HTTP RPC succeeded; two real stdio MCP clients and two Telnet protocol clients connected with no gateway login. While one execute waited, the other MCP client and both Telnet clients sent harmless echo commands. Both Telnet clients saw all markers, independent MCP readers returned identical bytes, and execute reported four additional writes. No reboot, flash or configuration change was performed.
- Actual Xshell UI, USB unplug/replug, long-duration stress and real COM8 remain untested; no COM8 hardware was opened. Board-side login, if any, is unchanged.
- GitHub Actions for v0.3: see repository Actions; the above is local test evidence.

## Historical v0.2.0 — free concurrent input/output

- Windows / Python 3.12.10: 24 automated tests passed. Simulated serial tests explicitly assert all execute calls write before any response, two authenticated Telnet clients write while MCP execute waits, former takeover/control keys pass through, timeout does not gate later input, and independent readers see identical RX.
- Windows COM3, 115200 / 8N1 / no flow control: verified with two real stdio MCP client processes and two Telnet protocol clients. One execute deliberately waited for a later marker; while it was still waiting, the other MCP client and both Telnet clients immediately sent harmless echo commands without any acquisition step. All outputs reached both Telnet clients and the MCP readers returned identical bytes. The waiting execute reported four additional writes and completed on the final marker. No reboot, flash or configuration change was performed.
- Installed daemon restarted under the existing current-user logon task. COM3 reopened and returned input_mode=shared and Telnet 127.0.0.1:2303.
- Actual Xshell application UI: not manually verified. Telnet protocol tests do not establish Xshell UI acceptance.
- USB unplug/replug, long-duration stress and real COM8 concurrency: not tested. No COM8 hardware was opened.
- GitHub Actions v0.2 run 43167188617 completed successfully. Its token-based authentication was removed in v0.3.

## Historical v0.1.0

28 local tests and six Windows/Ubuntu × Python 3.11/3.12/3.13 CI jobs passed for initial source commit 9eea3bf (run 43165294313). That version used control leases and command transaction queues; those semantics were rejected and replaced in v0.2, so the old tests are not evidence of current concurrency behavior.

Current-user logon startup was manually started and checked; a full sign-out/sign-in or reboot remains untested. Linux CI uses simulated I/O, not Linux hardware support.
