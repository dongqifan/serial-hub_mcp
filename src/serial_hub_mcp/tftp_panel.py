"""Window-owned, manually started TFTP listeners; no automatic network activity."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .dialog_layout import show_centered


class LocalServices:
    def __init__(self, on_change=None):
        self.instances = {}  # (board UUID, name) -> (confirmed configuration, instance)
        self.on_change = on_change

    def start(self, board_id, config):
        from .tftp_server import TftpServer
        key = board_id, config['name']
        if key in self.instances:
            previous, instance = self.instances[key]
            if instance.running:
                if previous != config:
                    raise ValueError('配置已变更，请先停止原服务。')
                return
            self.stop(key)
        server = TftpServer(config['ip'], config['port'], config['directory'])
        self.instances[key] = (dict(config), server)
        try:
            server.start()
        except Exception:
            self.stop(key)
            raise
        if self.on_change:
            self.on_change()

    def stop(self, key):
        if key in self.instances:
            self.instances[key][1].stop()
            del self.instances[key]
            if self.on_change:
                self.on_change()

    def snapshot(self):
        """Status only: report actual running listeners, never other board profile fields."""
        return [{'board_id': board_id, 'name': name, 'ip': cfg['ip'], 'port': cfg['port'],
                 'directory': cfg['directory']} for (board_id, name), (cfg, server) in self.instances.items()
                if server.running]

    def active(self, boards):
        keys = set()
        for board in boards:
            for config in board.get('tftp_servers', []):
                key = board['id'], config['name']
                entry = self.instances.get(key)
                if entry and entry[0] == config and entry[1].running:
                    keys.add(key)
        return keys

    def reconcile(self, projects):
        """Stop only this window's listeners whose saved configuration no longer matches."""
        configs = {(board['id'], cfg['name']): cfg for project in projects for board in project.boards
                   for cfg in board.get('tftp_servers', [])}
        for key, (old, instance) in list(self.instances.items()):
            if old != configs.get(key) or not instance.running:
                self.stop(key)

    def close(self):
        self.on_change = None
        for key in list(self.instances):
            self.stop(key)


class ServicePanel(tk.Toplevel):
    def __init__(self, root, board, services):
        super().__init__(root)
        self.withdraw()
        self.title(f"{board['name']} | 本机 TFTP 服务")
        self.transient(root)
        self.board, self.services = board, services
        self.configs = board.get('tftp_servers', [])
        self.notice = tk.StringVar(self, value='关闭此面板不会停止服务；退出开发板控制台会停止本窗口的所有服务。'
                                   if self.configs else '尚未配置服务。请关闭此面板，在“编辑资料”中添加本机 TFTP 服务。')
        self.timer = None
        body = ttk.Frame(self, padding=14)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f"{board['name']} 的本机 TFTP 服务", font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        table = ttk.Frame(body)
        table.pack(fill='both', expand=True, pady=(10, 6))
        self.rows = ttk.Treeview(table, columns=('name', 'ip', 'port', 'state', 'directory'), show='headings',
                                 height=5, selectmode='browse')
        for key, title, width in (('name','服务',110), ('ip','绑定 IP',140), ('port','UDP 端口',75),
                                  ('state','本窗口状态',90), ('directory','只读目录',230)):
            self.rows.heading(key, text=title, anchor='w')
            self.rows.column(key, width=width, minwidth=50, anchor='w')
        scroll = ttk.Scrollbar(table, command=self.rows.yview)
        self.rows.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.rows.pack(fill='both', expand=True)
        for i, cfg in enumerate(self.configs):
            self.rows.insert('', 'end', iid=str(i), values=(cfg['name'], cfg['ip'], cfg['port'], '', cfg['directory']))
        if self.configs:
            self.rows.selection_set('0')
        self.rows.bind('<<TreeviewSelect>>', lambda _: self.update_state())
        ttk.Label(body, textvariable=self.notice, wraplength=650).pack(anchor='w', pady=6)
        actions = ttk.Frame(body)
        actions.pack(fill='x')
        self.start_button = ttk.Button(actions, text='启动所选服务', command=lambda: self.change(True))
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='停止所选服务', command=lambda: self.change(False))
        self.stop_button.pack(side='left', padx=6)
        ttk.Button(actions, text='关闭面板', command=self.destroy).pack(side='right')
        show_centered(self, root)
        self.watch()

    def update_state(self):
        active = self.services.active([self.board])
        for i, cfg in enumerate(self.configs):
            running = (self.board['id'], cfg['name']) in active
            self.rows.set(str(i), 'state', '运行中' if running else '未启动')
        ids = self.rows.selection()
        running = bool(ids and self.rows.set(ids[0], 'state') == '运行中')
        self.start_button.configure(state='normal' if ids and not running else 'disabled')
        self.stop_button.configure(state='normal' if running else 'disabled')

    def watch(self):
        self.update_state()
        self.timer = self.after(750, self.watch)

    def change(self, start):
        ids = self.rows.selection()
        if not ids:
            return
        cfg = self.configs[int(ids[0])]
        key = self.board['id'], cfg['name']
        try:
            if start:
                if not messagebox.askyesno('启动只读 TFTP 服务',
                        f"开发板：{self.board['name']}\n服务：{cfg['name']}\n绑定 IP：{cfg['ip']}\n"
                        f"UDP 端口：{cfg['port']}\n只读目录：{cfg['directory']}\n\n"
                        'TFTP 没有身份验证或加密。能访问此接口的设备都可能下载公开目录中的文件，'
                        '并不限于本开发板。确认目录仅含可公开的固件，并启动？', default='no', parent=self):
                    return
                self.services.start(key[0], cfg)
            else:
                self.services.stop(key)
            self.notice.set(f"{cfg['name']} {'已启动。' if start else '已停止。'}")
        except Exception as exc:
            self.notice.set(f"{cfg['name']} 操作失败：{exc}")
        self.update_state()

    def destroy(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
        super().destroy()
