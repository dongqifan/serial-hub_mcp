"""Open the shared registration dialog in the installed Serial Hub environment."""
from pathlib import Path
import subprocess
import sys


if __name__ == '__main__':
    exe = Path(__file__).resolve().parents[1] / '.venv' / 'Scripts' / 'python.exe'
    if not exe.is_file():
        from tkinter import messagebox
        messagebox.showerror('Serial Hub', 'Run scripts/install.ps1 first.')
        raise SystemExit(1)
    raise SystemExit(subprocess.call([str(exe), '-I', '-m', 'serial_hub_mcp.desktop_registration', *sys.argv[1:]]))
