# Validation record

No customer data, terminal output, COM logs or credentials belong in this document.

- Automated tests: passed locally on Windows, Python 3.12.10. Includes two real stdio MCP client processes through an authenticated HTTP daemon with simulated serial I/O. See test suite for current case count.
- Windows COM3 hardware: verified at 115200 / 8N1 / no flow control. Two independent MCP adapter processes issued harmless shell `echo` markers concurrently; both transactions completed, independent readers returned identical RX, and a third Telnet protocol client observed both outputs. Telnet acquired input control, sent another harmless marker, confirmed idle and released control. Closing MCP clients did not disconnect serial capture. No reboot, flash or configuration change was used.
- Real hardware human-preemption, USB unplug/replug, long-running stress and COM8 concurrency: not tested. Mock tests cover preemption, disconnected reads and independent ports.
- Xshell application: not yet manually verified (protocol-level Telnet tests are not Xshell UI verification).
- COM8: not tested; no authorization to operate it has been assumed.
- OpenCode: global MCP registration loaded successfully; an actual OpenCode tool call executed a harmless marker on COM3.
- Windows logon task: registered for current user and manually started successfully with pythonw.exe; loopback API was reachable and COM3 reopened through the existing OpenCode MCP adapter. Full sign-out/sign-in or reboot startup has not been tested.
- GitHub Actions: workflow included; a local pass is not evidence of remote CI success.
