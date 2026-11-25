"""Local daemon controls and explicit stdio registration; neither proves a client connection."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk

from .desktop_registration import choose_config, register, stdio_config, stdio_python
from .dialog_layout import show_centered
from .gui_service import console_status


class ConsoleServicePanel(tk.Toplevel):
    def __init__(self, console, call, start):
        super().__init__(console.root)
        self.withdraw()
        self.transient(console.root)
        self.title('MCP 服务/接入')
        self.console, self.call, self.start = console, call, start
        self.exe = stdio_python()
        self.identity = None
        self.pending = False
        self.state = tk.StringVar(self, '尚未查询本机服务。')
        self.notice = tk.StringVar(self)
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='本机共享服务', font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(body, textvariable=self.state, justify='left', wraplength=580).pack(anchor='w', pady=8)
        controls = ttk.Frame(body)
        controls.pack(fill='x')
        self.query_button = ttk.Button(controls, text='查询状态', command=self.query)
        self.query_button.pack(side='left')
        self.start_button = ttk.Button(controls, text='启动 / 复用', command=lambda: self.query(start=True))
        self.start_button.pack(side='left', padx=6)
        self.stop_button = ttk.Button(controls, text='停止空闲服务…', command=self.stop)
        self.stop_button.pack(side='left')
        ttk.Separator(body).pack(fill='x', pady=14)
        ttk.Label(body, text='客户端接入', font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(body, text='每个支持 stdio 的客户端只需配置一次 serial_hub。切换项目不用重新注册；'
                  '在主窗口复制项目 ID 给 AI 即可。', wraplength=580, justify='left').pack(anchor='w', pady=(8, 3))
        ttk.Label(body, text=f'命令：{self.exe}\n参数：-I -m serial_hub_mcp.mcp_server', wraplength=580,
                  justify='left').pack(anchor='w', pady=3)
        ttk.Label(body, text='服务可用不代表客户端已注册或连接。写入配置后，请重启客户端，在其工具列表中确认。',
                  wraplength=580, justify='left').pack(anchor='w', pady=(3, 8))
        access = ttk.Frame(body)
        access.pack(fill='x')
        ttk.Button(access, text='复制通用配置', command=self.copy_config).pack(side='left')
        self.merge_button = ttk.Button(access, text='选择并合并 Claude Desktop 配置…', command=self.merge_config)
        self.merge_button.pack(side='left', padx=6)
        ttk.Button(access, text='关闭', command=self.dismiss).pack(side='right')
        ttk.Label(body, textvariable=self.notice, wraplength=580, justify='left').pack(anchor='w', pady=(8, 0))
        self.protocol('WM_DELETE_WINDOW', self.dismiss)
        self.bind('<Escape>', lambda _: self.dismiss())
        self.show_buttons()

    def present(self):
        if not self.winfo_viewable():
            show_centered(self, self.console.root)
            self.wait_visibility()
        self.lift()
        self.grab_set()
        self.query()  # Observation only, including when opening again after a stop.

    def dismiss(self):
        # Start/stop may still be in flight. Its result must update the main
        # window even if this panel was closed in the meantime.
        self.grab_release()
        self.withdraw()

    def show_buttons(self):
        status = self.identity
        for button in (self.query_button, self.start_button, self.merge_button):
            button.configure(state='disabled' if self.pending else 'normal')
        can_stop = (not self.pending and status and 'idle_stop' in status['capabilities']
                    and not status['open_ports'] and not status.get('stopping'))
        self.stop_button.configure(state='normal' if can_stop else 'disabled')

    def request(self, work, success, failure, pending):
        if self.pending:
            return
        def finish(callback, arg):
            self.pending = False
            if self.winfo_exists():
                self.show_buttons()
                callback(arg)
        if self.console.submit(work, None, lambda result: finish(success, result),
                               lambda exc: finish(failure, exc), lock_profiles=False):
            self.pending = True
            self.notice.set(pending)
        else:
            self.notice.set('另一项操作正在进行，请稍后再试。')
        self.show_buttons()

    def query(self, start=False):
        def work():
            inventory = self.start() if start else None
            try:
                status = console_status(self.call)
            except ValueError as exc:
                return inventory, None, str(exc)
            return inventory, status, ''

        def show(result):
            inventory, status, explanation = result
            if start:
                self.console.set_inventory(inventory)
                self.console.report_tftp()
            self.identity = status
            if status is None:
                self.state.set(explanation)
            else:
                ports = '、'.join(status['open_ports']) or '无'
                caps = '、'.join(status['capabilities']) or '无'
                state = '正在停止' if status.get('stopping') else '可访问'
                self.state.set(f"{status['name']}  {status['version']}（{state}）\n实例：{status['instance_id']}\n"
                               f'能力：{caps}\n已打开串口：{ports}')
            if status and status.get('stopping'):
                self.notice.set('此实例正在停止，请稍后重新查询。')
            elif status and 'idle_stop' not in status['capabilities']:
                self.notice.set('此实例未提供安全停止能力；不会强制停止进程。')
            elif status and status['open_ports']:
                self.notice.set('已打开串口，不能停止共享服务。')
            else:
                self.notice.set('')
            self.show_buttons()

        def failed(exc):
            self.identity = None
            self.console.set_inventory(None)
            self.state.set('本机服务不可用。项目档案仍可离线编辑。')
            self.notice.set(str(exc))
            self.show_buttons()
        self.request(work, show, failed, '正在启动或复用本机服务…' if start else '正在查询本机服务…')

    def stop(self):
        status = self.identity
        if (self.pending or not status or status['open_ports'] or status.get('stopping')
                or 'idle_stop' not in status['capabilities']):
            return
        if self.console.services.snapshot():
            self.notice.set('本窗口仍在提供 TFTP 服务。请先在对应开发板上停止服务。')
            return
        instance = status['instance_id']
        if not messagebox.askyesno('停止空闲共享服务',
                f"停止 {status['name']} {status['version']}？\n实例：{instance}\n\n"
                '其他会话将不能查询此服务，直到重新启动。请确认不再需要它。'
                '如果实例或占用状态已改变，请求会被拒绝，不会强制释放串口。', default='no', parent=self):
            return
        def work():
            reply = self.call('console_stop', {'instance_id': instance})
            if not isinstance(reply, dict) or reply.get('stopping') is not True:
                raise ValueError('服务未确认停止请求；请重新查询，不要重复提交。')

        def stopped(_):
            self.identity = None
            self.console.set_inventory(None)
            self.state.set('服务已接受停止请求。可查询状态，或手动启动 / 复用。')
            self.notice.set('刷新状态不会启动服务。')
            self.console.status.set(self.state.get())
            self.show_buttons()

        def failed(exc):
            self.identity = None
            self.state.set('未确认停止，请重新查询服务状态。')
            self.notice.set(str(exc))
            self.show_buttons()
        self.request(work, stopped, failed, '正在请求停止此实例…')

    def copy_config(self):
        try:
            config = stdio_config(self.exe)
        except ValueError as exc:
            self.notice.set(str(exc))
            return
        text = json.dumps(config, ensure_ascii=False, indent=2)
        self.console.clipboard(text, '已复制 serial_hub 的 stdio 配置。')
        self.notice.set('已复制通用配置。请按客户端格式合并，不要替换已有设置。')

    def merge_config(self):
        try:
            path = choose_config(self)
        except (OSError, ValueError) as exc:
            self.notice.set(str(exc))
            return
        if path is not None:
            self.request(lambda: register(path, self.exe), self.notice.set,
                         lambda exc: self.notice.set(f'未写入：{exc}'), '正在合并所选文件…')
