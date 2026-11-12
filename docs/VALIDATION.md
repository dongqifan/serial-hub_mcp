# Validation record

No customer data, raw terminal output, serial logs or credentials belong here.

## v0.2.0 — free concurrent input/output

- Windows / Python 3.12.10: 24 automated tests passed. Simulated serial tests explicitly assert all execute calls write before any response, two authenticated Telnet clients write while MCP execute waits, former takeover/control keys pass through, timeout does not gate later input, and independent readers see identical RX.
- Windows COM3, 115200 / 8N1 / no flow control: verified with two real stdio MCP client processes and two Telnet protocol clients. One execute deliberately waited for a later marker; while it was still waiting, the other MCP client and both Telnet clients immediately sent harmless echo commands without any acquisition step. All outputs reached both Telnet clients and the MCP readers returned identical bytes. The waiting execute reported four additional writes and completed on the final marker. No reboot, flash or configuration change was performed.
- Installed daemon restarted under the existing current-user logon task. COM3 reopened and returned input_mode=shared and Telnet 127.0.0.1:2303.
- Actual Xshell application UI: not manually verified. Telnet protocol tests do not establish Xshell UI acceptance.
- USB unplug/replug, long-duration stress and real COM8 concurrency: not tested. No COM8 hardware was opened.
- GitHub Actions for v0.2: see repository Actions; local passing tests are not remote CI evidence.

## Historical v0.1.0

28 local tests and six Windows/Ubuntu × Python 3.11/3.12/3.13 CI jobs passed for initial source commit 9eea3bf (run 43165294313). That version used control leases and command transaction queues; those semantics were rejected and replaced in v0.2, so the old tests are not evidence of current concurrency behavior.

Current-user logon startup was manually started and checked; a full sign-out/sign-in or reboot remains untested. Linux CI uses simulated I/O, not Linux hardware support.
