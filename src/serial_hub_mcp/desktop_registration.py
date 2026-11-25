"""The local stdio entry and the opt-in Claude Desktop config merge shared by both UIs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from .settings import api_url


CONFIG_NAME = 'claude_desktop_config.json'


def stdio_python() -> Path:
    # The console normally runs under pythonw, which has no stdio streams. Keep
    # the venv path (don't resolve symlinks to the system interpreter).
    exe = Path(sys.executable)
    return exe.with_name('python.exe') if exe.name.lower() == 'pythonw.exe' else exe


def stdio_config(exe: Path) -> dict:
    entry = {'command': str(exe), 'args': ['-I', '-m', 'serial_hub_mcp.mcp_server']}
    # GUI and client can inherit different environments. Forward only the
    # normalized loopback port, never the GUI's environment (or its secrets).
    try:
        port = api_url().rsplit(':', 1)[1]
    except ValueError:
        raise ValueError('SERIAL_HUB_API_PORT 必须为 1 至 65535 之间的整数。') from None
    if port != '8766':
        entry['env'] = {'SERIAL_HUB_API_PORT': port}
    return {'mcpServers': {'serial_hub': entry}}


def _unique(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('配置有重复的 JSON 字段，原文件未修改。')
    return result


def _read(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def register(path: Path, exe: Path) -> str:
    if path.name.lower() != CONFIG_NAME:
        raise ValueError(f'请选择 Claude Desktop 开发者设置中的 {CONFIG_NAME}。')
    before = _read(path)
    config = json.loads(before.decode('utf-8-sig'), object_pairs_hook=_unique) if before is not None else {}
    if not isinstance(config, dict) or not isinstance(config.setdefault('mcpServers', {}), dict):
        raise ValueError('Claude Desktop 配置格式无效，原文件未修改。')
    servers = config['mcpServers']
    entry = stdio_config(exe)['mcpServers']['serial_hub']
    if 'serial_hub' in servers:
        if servers['serial_hub'] == entry:
            return '所选配置已包含相同的 serial_hub，未改动文件。'
        if servers['serial_hub'] == {**entry, 'args': entry['args'][1:]}:
            return '所选配置已包含本机 serial_hub（旧版命令，不含 -I）。仍可使用，未改动文件。'
        raise ValueError('所选配置已有不同的 serial_hub，未覆盖。请在客户端配置中手动核对。')
    servers['serial_hub'] = entry
    backup = None
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
    try:
        temp.write_text(json.dumps(config, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
        if before is None:
            # Don't overwrite a config another process created after our read.
            if os.name == 'nt':
                os.rename(temp, path)
            else:
                os.link(temp, path)
        else:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.bak', delete=False) as f:
                backup = Path(f.name)
                f.write(before)
            if _read(path) != before:
                raise ValueError('配置文件已变更，未覆盖。请重新选择。')
            os.replace(temp, path)
    except BaseException:
        if backup is not None:
            backup.unlink(missing_ok=True)
        raise
    finally:
        temp.unlink(missing_ok=True)
    return ('已将 serial_hub 写入所选配置。请完全退出并重新打开 Claude Desktop，在客户端核对工具。'
            + (f'\n原文件备份：{backup}' if backup else ''))


def choose_config(parent, selected: str | None = None) -> Path | None:
    from tkinter import filedialog, messagebox
    if selected is None:
        selected = filedialog.asksaveasfilename(
            parent=parent, title='选择 Claude Desktop > 设置 > 开发者 > 编辑配置 中的文件',
            initialfile=CONFIG_NAME, filetypes=[('Claude Desktop 配置', CONFIG_NAME)], confirmoverwrite=False)
    if not selected:
        return None
    path = Path(selected)
    if path.name.lower() != CONFIG_NAME:
        raise ValueError(f'只支持 {CONFIG_NAME}，未写入文件。')
    if not messagebox.askyesno('合并 Claude Desktop 配置',
            f'仅将 serial_hub 添加到以下文件？\n{path}\n\n保留其他设置；存在不同的同名项时不会覆盖。'
            '修改已有文件前，会在同目录保存完整备份；文件不存在时会新建。', parent=parent, default='no'):
        return None
    return path


def main():
    from tkinter import Tk, messagebox
    root = Tk()
    root.withdraw()
    try:
        if len(sys.argv) > 2:
            raise ValueError(f'只接受一个可选的 {CONFIG_NAME} 路径。')
        path = choose_config(root, sys.argv[1] if len(sys.argv) == 2 else None)
        if path is not None:
            messagebox.showinfo('Serial Hub', register(path, stdio_python()), parent=root)
    except (ValueError, OSError) as exc:
        messagebox.showerror('Serial Hub — 配置未写入', str(exc), parent=root)
        raise SystemExit(1) from None
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
