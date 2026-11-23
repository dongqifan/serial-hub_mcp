"""Merge Serial Hub into the user-selected Claude Desktop developer config."""
import json
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime


def register(path, exe):
    if path.name.lower() != 'claude_desktop_config.json':
        raise ValueError('Select claude_desktop_config.json from Settings > Developer > Edit config.')
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError('Duplicate JSON keys; no changes made.')
        return result
    before = path.read_bytes() if path.exists() else None
    config = json.loads(before.decode('utf-8-sig'), object_pairs_hook=unique) if before is not None else {}
    if not isinstance(config, dict) or not isinstance(config.setdefault('mcpServers', {}), dict):
        raise ValueError('Invalid Desktop config; no changes made.')
    servers = config['mcpServers']
    entry = {'command': str(exe), 'args': ['-m', 'serial_hub_mcp.mcp_server']}
    if 'serial_hub' in servers:
        if servers['serial_hub'] == entry:
            return 'Serial Hub is already registered.'
        raise ValueError('A different serial_hub entry exists; it has not been changed.')
    servers['serial_hub'] = entry
    backup = None
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
    try:
        temp.write_text(json.dumps(config, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
        if (path.read_bytes() if path.exists() else None) != before:
            raise ValueError('Config changed; retry.')
        if before is not None:
            backup = path.with_suffix('.json.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
            shutil.copy2(path, backup)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return 'Registered serial_hub. Fully quit and reopen Claude Desktop.' + (f'\nBackup: {backup}' if backup else '')


if __name__ == '__main__':
    import sys
    from tkinter import Tk, filedialog, messagebox
    root = Tk()
    root.withdraw()
    try:
        exe = Path(__file__).resolve().parents[1] / '.venv' / 'Scripts' / 'python.exe'
        if not exe.is_file():
            raise ValueError('Run scripts/install.ps1 first.')
        file = sys.argv[1] if len(sys.argv) == 2 else filedialog.asksaveasfilename(
            title='Select the config from Claude Desktop > Settings > Developer > Edit config',
            initialfile='claude_desktop_config.json', filetypes=[('JSON config', '*.json')], confirmoverwrite=False)
        if file:
            messagebox.showinfo('Serial Hub', register(Path(file), exe))
    except (ValueError, OSError) as exc:
        messagebox.showerror('Serial Hub - not registered', str(exc))
        sys.exit(1)
    finally:
        root.destroy()
