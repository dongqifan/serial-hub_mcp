"""Desktop front panel for the shared daemon. Never opens a serial device locally."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import messagebox, ttk

from .cli import rpc
from .core import HubError, PortConfig
from .gui_service import ensure_service
from .project_actions import READY, claim_group, group_hint, member_state, release_ports
from .project_editor import edit_project
from .projects import ALL_PORTS, load_projects, save_projects

PANEL, INK, ACCENT, PAPER, MUTED = '#edf2f6', '#21394c', '#186579', '#ffffff', '#4a6271'


class Console:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.busy = False
        self.available = {}
        self.opened = {}
        self.inventory = {'available': [], 'open': []}
        self.known = False
        self.project = tk.StringVar(value=ALL_PORTS)
        try:
            self.projects = load_projects()
        except (ValueError, OSError) as exc:
            self.projects = {}
            messagebox.showerror('无法读取项目组', str(exc), parent=root)
        self.report = ''
        self.status = tk.StringVar(value='正在连接本机共享服务…')
        self.selection = tk.StringVar(value='未选择串口')
        self.baud = tk.StringVar(value='115200')
        self.details = tk.StringVar()
        root.title('Serial Hub | 串口控制台')
        root.minsize(690, 390)
        root.configure(bg=PANEL)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.configure('Ports.Treeview', rowheight=32)
        header = tk.Frame(root, bg=INK, padx=20, pady=12)
        header.pack(fill='x')
        tk.Label(header, text='串口控制台', bg=INK, fg=PAPER,
                 font=('Microsoft YaHei UI', 16, 'bold')).pack(side='left')
        self.refresh = ttk.Button(header, text='刷新串口', command=self.reload)
        self.refresh.pack(side='right')
        self.all_release = ttk.Button(header, text='全部释放', command=self.release_all)
        self.all_release.pack(side='right', padx=8)
        body = tk.Frame(root, bg=PANEL, padx=20, pady=14)
        body.pack(fill='both', expand=True)
        presets = tk.Frame(body, bg=PANEL)
        presets.pack(fill='x', pady=(0, 10))
        tk.Label(presets, text='项目组', fg=INK, bg=PANEL).pack(side='left')
        self.picker = ttk.Combobox(presets, textvariable=self.project, width=18, state='readonly',
                                   values=(ALL_PORTS, *self.projects))
        self.picker.pack(side='left', padx=(6, 4))
        self.picker.bind('<<ComboboxSelected>>', lambda _: self.switch_project())
        self.new = ttk.Button(presets, text='新建组', width=6, command=lambda: self.manage(None))
        self.new.pack(side='left')
        self.edit = ttk.Button(presets, text='编辑组', width=6, command=lambda: self.manage(self.project.get()))
        self.edit.pack(side='left', padx=3)
        self.group_copy = ttk.Button(presets, text='复制本组给 AI', command=self.copy_group)
        self.group_copy.pack(side='right')
        self.group_open = ttk.Button(presets, text='占用本组', command=self.claim_project)
        self.group_open.pack(side='right', padx=6)
        listing = ttk.Frame(body)
        listing.pack(fill='both', expand=True)
        columns = [('name', '串口', 80), ('state', '本服务状态', 110), ('baud', '波特率', 85),
                   ('endpoint', 'Telnet 地址（本机）', 170), ('device', '设备', 160)]
        self.table = ttk.Treeview(listing, columns=[c[0] for c in columns], show='headings',
                                  selectmode='browse', height=6, style='Ports.Treeview')
        for key, label, width in columns:
            self.table.heading(key, text=label, anchor='w')
            self.table.column(key, width=width, minwidth=width, stretch=(key == 'device'))
        scroll = ttk.Scrollbar(listing, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.table.pack(fill='both', expand=True)
        self.table.bind('<<TreeviewSelect>>', lambda _: self.update_controls())
        controls = tk.Frame(body, bg=PANEL, pady=8)
        controls.pack(fill='x')
        tk.Label(controls, textvariable=self.selection, fg=ACCENT, bg=PANEL,
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(side='left')
        tk.Label(controls, text='波特率', fg=INK, bg=PANEL).pack(side='left', padx=(16, 4))
        self.speed = ttk.Combobox(controls, width=9, textvariable=self.baud,
                                  values=('9600', '19200', '38400', '57600', '115200', '230400', '921600'))
        self.speed.pack(side='left')
        self.release = ttk.Button(controls, text='释放串口', command=self.release_port)
        self.release.pack(side='right')
        self.claim = ttk.Button(controls, text='占用串口', command=self.claim_port)
        self.claim.pack(side='right', padx=6)
        sharing = tk.Frame(body, bg=PANEL)
        sharing.pack(fill='x')
        tk.Label(sharing, textvariable=self.details, fg=MUTED, bg=PANEL, anchor='w',
                 justify='left', wraplength=400).pack(side='left', fill='x', expand=True)
        self.handoff = ttk.Button(sharing, text='复制给 AI', command=self.copy_hint)
        self.handoff.pack(side='right')
        self.copy = ttk.Button(sharing, text='复制 Telnet 地址', command=self.copy_endpoint)
        self.copy.pack(side='right', padx=6)
        tk.Label(body, textvariable=self.status, fg=INK, bg=PANEL, anchor='w',
                 justify='left', wraplength=630).pack(fill='x', pady=(14, 2))
        footer = tk.Frame(body, bg=PANEL)
        footer.pack(fill='x')
        tk.Label(footer, text='关闭窗口不会释放串口。', fg=MUTED, bg=PANEL, anchor='w').pack(side='left')
        self.show_report = ttk.Button(footer, text='上次批量结果', command=self.view_report)
        self.show_report.pack(side='right')
        self.reload()

    def selected(self):
        ids = self.table.selection()
        return ids[0] if ids else None

    def switch_project(self):
        self.render(self.inventory)
        self.status.set(f'当前视图：{self.project.get()}。切换项目不会占用或释放串口。')

    def manage(self, current):
        try:
            latest = load_projects()
            if current and current not in latest:
                raise ValueError('该项目组已在其他窗口删除，请重新打开控制台。')
            result = edit_project(self.root, latest, current, sorted(self.available.keys() | self.opened.keys()))
            if result is None:
                return
            updated, choice = result
            if load_projects() != latest:
                raise ValueError('配置已在其他窗口修改，请重新编辑。')
            save_projects(updated)
        except (ValueError, OSError) as exc:
            messagebox.showerror('未保存项目组', str(exc), parent=self.root)
            return
        self.projects = updated
        self.picker.configure(values=(ALL_PORTS, *updated))
        self.project.set(choice)
        self.switch_project()

    def claim_project(self):
        project = self.project.get()
        members = self.projects.get(project)
        if members:
            self.run(lambda: claim_group(rpc, members), f'正在占用项目组“{project}”…', batch=project)

    def copy_group(self):
        project = self.project.get()
        members = self.projects.get(project)
        if members:
            def copy(inventory):
                self.copy_text(group_hint(project, members, inventory), f'已复制“{project}”的最新串口状态。')
            self.run(lambda: (rpc('list_ports', {}), ''), '正在核对本组连接状态…', after=copy)

    def release_all(self):
        def confirm(inventory):
            names = tuple(item['port'] for item in inventory['open'])
            if not names:
                self.status.set('本服务没有持有任何串口。')
                return
            prompt = (f'将释放本服务持有的 {len(names)} 个物理串口（不限于当前组）：\n'
                      + '、'.join(names) + '\n\n这些端口的所有项目将停止采集。请确认其他会话不再需要日志。'
                      '\n有 Telnet 客户端或活跃执行的端口不会强制释放。继续？')
            if messagebox.askyesno('全部释放', prompt, parent=self.root, default='no'):
                self.run(lambda: release_ports(rpc, names), '正在逐口释放…', batch='全部释放')
        self.run(lambda: (rpc('list_ports', {}), ''), '正在查询所有已占用串口…', after=confirm)

    def view_report(self):
        window = tk.Toplevel(self.root)
        window.title('上次批量结果')
        from tkinter.scrolledtext import ScrolledText
        text = ScrolledText(window, width=70, height=15, wrap='word', padx=12, pady=8)
        text.pack(fill='both', expand=True)
        text.insert('1.0', self.report)
        text.configure(state='disabled')

    def update_controls(self):
        name = self.selected()
        item = self.opened.get(name)
        group = self.projects.get(self.project.get())
        member = next((m for m in group or [] if m['port'] == name), None)
        ready = item and item['connected'] and (not member or member_state(member, self.inventory) == READY)
        can_open = not self.busy and name in self.available and not item
        idle = not self.busy
        for widget, enabled in ((self.new, idle), (self.edit, idle and group is not None),
                                (self.group_open, idle and self.known and group),
                                (self.group_copy, idle and self.known and group),
                                (self.all_release, idle and self.known), (self.show_report, bool(self.report))):
            widget.configure(state='normal' if enabled else 'disabled')
        self.picker.configure(state='readonly' if idle else 'disabled')
        self.selection.set(name or '未选择串口')
        self.speed.configure(state='normal' if can_open and not member else 'disabled')
        if member:
            self.baud.set(str(member['baudrate']))
        self.claim.configure(state='normal' if can_open else 'disabled')
        self.release.configure(state='normal' if item and not self.busy else 'disabled')
        self.copy.configure(state='normal' if ready and item['telnet'] and not self.busy else 'disabled')
        self.handoff.configure(state='normal' if ready and not self.busy else 'disabled')
        self.refresh.configure(state='disabled' if self.busy else 'normal')
        if item:
            if not member:
                self.baud.set(str(item['config']['baudrate']))
            self.details.set(f"Telnet 客户端：{item['terminal_clients']}    活跃执行：{item['active_executions']}"
                             if ready else ('当前连接参数与本组不符，未更改连接。' if item['connected'] else
                                            '设备连接已断开。请释放后重新占用。'))
        else:
            self.details.set('占用参数：8N1，无流控，DTR / RTS 低。' if name else '连接设备后点击“刷新串口”。')

    def render(self, inventory):
        selected = self.selected()
        self.available = {p['port']: p for p in inventory['available']}
        self.opened = {p['port']: p for p in inventory['open']}
        self.inventory = inventory
        self.table.delete(*self.table.get_children())
        group = self.projects.get(self.project.get())
        members = {m['port']: m for m in group or []}
        self.table.heading('device', text='设备角色' if group is not None else '设备')
        self.table.heading('baud', text='预设波特率' if group is not None else '波特率')
        names = list(members) if group is not None else sorted(self.available.keys() | self.opened.keys(),
                                                               key=lambda n: (len(n), n))
        for name in names:
            item = self.opened.get(name)
            ready = item and item['connected']
            state = ('已占用' if ready else '设备断开') if item else '未占用'
            rate = item['config']['baudrate'] if item else '—'
            device = self.available.get(name, {}).get('description') or '—'
            if name in members:
                m = members[name]
                state = member_state(m, inventory) if self.known else '状态未知'
                ready = state == READY
                rate, device = m['baudrate'], m['role'] or '—'
            self.table.insert('', 'end', iid=name, values=(name, state, rate,
                              (item['telnet'] if ready else None) or '—', device))
        if selected in names or len(names) == 1:
            self.table.selection_set(selected if selected in names else names[0])
        self.update_controls()

    def run(self, work, pending, *, after=None, batch=None):
        if self.busy:
            return
        self.busy = True
        self.status.set(pending)
        self.update_controls()
        future = self.worker.submit(work)

        def finish():
            if not future.done():
                self.root.after(60, finish)
                return
            self.busy = False
            try:
                inventory, notice = future.result()
                self.known = True
                if batch:
                    self.report = f'{batch}\n{notice}'
                    notice = notice.partition('\n')[0] + '  见“上次批量结果”。'
                self.render(inventory)
                self.status.set(notice or (f'已刷新：{len(self.table.get_children())} 个串口，'
                                           f'本服务持有 {len(self.opened)} 个。'))
                if after:
                    after(inventory)
            except Exception as exc:
                # Never advertise a stale Telnet endpoint on a failed refresh.
                self.known = False
                if batch:
                    self.report = f'{batch}\n{exc}'
                self.render({'available': [], 'open': []})
                self.status.set(f'状态未确认，请刷新串口。{str(exc).partition(chr(10))[0]}')

        self.root.after(60, finish)

    def reload(self):
        self.run(lambda: (ensure_service(), ''), '正在查询串口…')

    def change(self, operation, args, success):
        def work():
            try:
                rpc(operation, args)
                notice = success
            except Exception as exc:
                # A timeout doesn't establish whether the device changed state. Never retry a mutation.
                notice = f'请核对当前状态：{exc}'
            return rpc('list_ports', {}), notice
        self.run(work, f"正在处理 {args['port']}…")

    def claim_port(self):
        name = self.selected()
        if self.busy or name not in self.available or name in self.opened:
            return
        try:
            config = PortConfig(baudrate=int(self.baud.get()))
            config.validate()
        except (ValueError, HubError):
            self.status.set('请输入 50 至 4000000 之间的整数波特率。')
            return
        self.change('open_port', {'port': name, 'config': vars(config)}, f'{name} 已占用，可复制连接信息。')

    def release_port(self):
        name = self.selected()
        if self.busy or name not in self.opened:
            return
        if messagebox.askyesno('释放串口', f'释放 {name} 将停止采集并关闭其 Telnet 入口。\n'
                               '请确认其他会话不再需要采集。继续释放？', parent=self.root):
            self.change('close_port', {'port': name}, f'{name} 已释放。')

    def copy_text(self, text, notice):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.set(notice)

    def copy_hint(self):
        name = self.selected()
        self.copy_text(f'{name} 已占用，请用已注册的 serial_hub MCP 查询 {name} 的 port_status。'
                       '确认已连接后用 read_output 读取现有输出，不要发送数据。', '提示已复制，可粘贴到 AI 会话。')

    def copy_endpoint(self):
        self.copy_text(self.opened[self.selected()]['telnet'], '地址已复制。在 Xshell 中选择 TELNET 协议。')

    def close(self):
        self.worker.shutdown(wait=False)
        self.root.destroy()


def main():
    root = tk.Tk()
    Console(root)
    root.mainloop()
