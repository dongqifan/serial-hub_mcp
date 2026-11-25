"""A desktop register of development boards. All hardware I/O stays in the shared daemon."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from .board_editor import edit_board
from .board_operations import board_hint, board_state, claim_boards, release_ports
from .boards import DEFAULT_AI_PROMPT, CatalogConflict, Project, load_catalog, new_board, new_project, save_catalog
from .cli import rpc
from .console_service_panel import ConsoleServicePanel
from .gui_service import TftpReporter, ensure_service, inventory_snapshot
from .project_actions import CONFLICT, IDLE, READY
from .projects import project_name
from .tftp_panel import LocalServices, ServicePanel

# A bench register: quiet navigation, a prominent board name and three labelled connections.
DECK, INK, PAPER, SIGNAL, MUTED = '#e8f0f1', '#193b43', '#ffffff', '#08665e', '#45616a'
EMPTY = {'open': [], 'available': []}


class BoardConsole:
    def __init__(self, root):
        self.root = root
        self.closed = False
        self.services = LocalServices(self.report_tftp)
        self.reporter = TftpReporter(rpc)
        self.heartbeat_id = None
        self.mcp_panel = None
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.busy = self.lock_profiles = False
        self.inv = EMPTY
        self.known = False
        self.report = ''
        self.poll_id = None
        self.config_ok = True
        try:
            self.catalog = load_catalog()
        except (ValueError, OSError) as exc:
            self.catalog, self.config_ok = None, False
            messagebox.showerror('无法读取开发板档案', str(exc), parent=root)
        self.project_id = self.projects[0].id if self.projects else None
        self.id_text = tk.StringVar(root)
        self.status = tk.StringVar(root)
        self.fields = {k: tk.StringVar(root) for k in ('name', 'serial', 'telnet', 'serial_user', 'web', 'web_user', 'tftp')}
        self.buttons = {}
        root.title('开发板控制台')
        root.minsize(790, 460)
        root.configure(bg=DECK)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.build()
        self.render()
        self.heartbeat()
        self.refresh(start=True)

    def report_tftp(self):
        self.reporter.publish(self.services.snapshot())

    def heartbeat(self):
        self.report_tftp()  # Even an empty set is a current observation from this window.
        self.heartbeat_id = self.root.after(5000, self.heartbeat)

    def button(self, frame, key, label, command, side='left'):
        widget = ttk.Button(frame, text=label, command=command)
        widget.pack(side=side, padx=2, pady=2)
        self.buttons[key] = widget
        return widget

    def build(self):
        root = self.root
        head = tk.Frame(root, bg=INK, padx=16, pady=9)
        head.pack(fill='x')
        tk.Label(head, text='开发板控制台', font=('Microsoft YaHei UI', 16, 'bold'),
                 fg=PAPER, bg=INK).pack(side='left')
        self.button(head, 'release_all', '全部释放', lambda: self.release(True), 'right')
        self.button(head, 'refresh', '刷新状态', self.refresh, 'right')
        self.button(head, 'mcp', 'MCP 服务/接入', self.show_mcp, 'right')
        body = tk.Frame(root, bg=DECK, padx=14, pady=10)
        body.pack(fill='both', expand=True)
        top = tk.Frame(body, bg=DECK)
        top.pack(fill='x')
        tk.Label(top, text='项目组', bg=DECK, fg=INK).pack(side='left')
        self.picker = ttk.Combobox(top, width=16, state='readonly')
        self.picker.pack(side='left', padx=6)
        self.picker.bind('<<ComboboxSelected>>', self.pick_project)
        self.button(top, 'add_group', '新建项目', lambda: self.change_project('new'))
        self.button(top, 'group_menu', '管理项目', self.project_menu)
        self.button(top, 'group_copy', '复制本组给 AI', lambda: self.handoff(True), 'right')
        self.button(top, 'group_claim', '占用本组串口', lambda: self.claim(True), 'right')
        identity = tk.Frame(body, bg=DECK)
        identity.pack(fill='x', pady=(0, 6))
        tk.Label(identity, text='项目 ID', bg=DECK, fg=MUTED).pack(side='left')
        self.id_entry = ttk.Entry(identity, textvariable=self.id_text, state='readonly', width=34,
                                  font='TkFixedFont', exportselection=False)
        self.id_entry.pack(side='left', padx=6)
        self.button(identity, 'copy_id', '复制项目 ID', self.copy_project_id)

        mid = tk.Frame(body, bg=DECK)
        mid.pack(fill='both', expand=True)
        left = tk.Frame(mid, bg=DECK)
        left.pack(side='left', fill='both', padx=(0, 14))
        tk.Label(left, text='项目内开发板', bg=DECK, fg=INK, anchor='w').pack(fill='x', pady=(4, 6))
        frame = ttk.Frame(left)
        frame.pack(fill='both', expand=True)
        self.listing = ttk.Treeview(frame, show='tree', selectmode='browse', height=8)
        self.listing.column('#0', width=150, minwidth=100, stretch=False)
        bar = ttk.Scrollbar(frame, orient='vertical', command=self.listing.yview)
        self.listing.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.listing.pack(fill='both', expand=True)
        self.listing.bind('<<TreeviewSelect>>', lambda _: self.details())
        self.button(left, 'add', '添加开发板', lambda: self.change_board('new'), 'bottom')

        detail = tk.Frame(mid, bg=PAPER, padx=16, pady=10)
        detail.pack(side='left', fill='both', expand=True)
        title = tk.Frame(detail, bg=PAPER)
        title.pack(fill='x')
        self.button(title, 'edit', '编辑资料', lambda: self.change_board('edit'), 'right')
        tk.Label(title, textvariable=self.fields['name'], font=('Microsoft YaHei UI', 16, 'bold'),
                 fg=SIGNAL, bg=PAPER, anchor='w').pack(fill='x')
        grid = tk.Frame(detail, bg=PAPER)
        grid.pack(fill='x', pady=(8, 6))
        grid.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((('serial', '串口'), ('telnet', 'Telnet 入口'), ('serial_user', '串口账号'),
                                            ('web', 'Web 登录地址'), ('web_user', 'Web 账号'), ('tftp', '本机 TFTP 服务'))):
            pad = (9, 0) if key in ('web', 'tftp') else (1, 0)
            tk.Label(grid, text=label, fg=MUTED, bg=PAPER, anchor='w').grid(row=row, column=0, sticky='nw', pady=pad)
            tk.Label(grid, textvariable=self.fields[key], fg=INK, bg=PAPER, anchor='w', justify='left',
                     wraplength=380).grid(row=row, column=1, padx=(12, 2), sticky='w', pady=pad)
        actions = tk.Frame(detail, bg=PAPER)
        actions.pack(fill='x', pady=(6, 0))
        self.button(actions, 'claim', '占用串口', lambda: self.claim(False))
        self.button(actions, 'release', '释放串口', lambda: self.release(False))
        self.button(actions, 'copy', '复制本板给 AI', lambda: self.handoff(False))
        self.button(actions, 'prompt', '编辑 AI 提示', self.edit_prompt)
        extra = tk.Frame(detail, bg=PAPER)
        extra.pack(fill='x')
        self.button(extra, 'start_tftp', '启动本板 TFTP', self.start_board_services)
        self.button(extra, 'services', '管理本机服务', self.show_services)
        self.button(extra, 'telnet', '复制 Telnet 地址', self.copy_telnet)
        self.button(extra, 'delete', '删除开发板', lambda: self.change_board('delete'), 'right')
        footer = tk.Frame(body, bg=DECK)
        footer.pack(fill='x', pady=(8, 0))
        self.button(footer, 'report', '操作明细', self.view_report, 'right')
        tk.Label(footer, textvariable=self.status, bg=DECK, fg=INK, anchor='w', justify='left',
                 wraplength=610).pack(side='left', fill='x', expand=True)

    @property
    def projects(self):
        return self.catalog.projects if self.catalog is not None else []

    def active_project(self):
        return next((p for p in self.projects if p.id == self.project_id), None)

    def pick_project(self, _event=None):
        index = self.picker.current()
        if 0 <= index < len(self.projects):
            self.project_id = self.projects[index].id
            self.render()

    def copy_project_id(self):
        project = self.active_project()
        if project:
            self.clipboard(project.id, f'已复制“{project.name}”的项目 ID，不含登录资料。')

    def active_boards(self):
        project = self.active_project()
        return project.boards if project else []

    def selected_board(self):
        ids = self.listing.selection()
        return next((b for b in self.active_boards() if ids and b['id'] == ids[0]), None)

    def render(self, selected=None):
        ids = self.listing.selection()
        self.picker.configure(values=[p.name for p in self.projects])
        if self.projects:
            index = next((i for i, p in enumerate(self.projects) if p.id == self.project_id), 0)
            self.project_id = self.projects[index].id
            self.picker.current(index)
        else:
            self.project_id = None
            self.picker.set('')
        self.id_text.set(self.project_id or '—')
        self.listing.delete(*self.listing.get_children())
        for b in self.active_boards():
            self.listing.insert('', 'end', iid=b['id'], text=b['name'])
        rows = self.listing.get_children()
        choice = selected or (ids[0] if ids else None)
        if rows:
            self.listing.selection_set(choice if choice in rows else rows[0])
            self.listing.see(self.listing.selection()[0])
        self.details()

    def live(self, b):
        return next((o for o in self.inv['open'] if b and o['port'] == b['port']), {})

    def details(self):
        b = self.selected_board()
        state = board_state(b, self.inv) if b and self.known else '状态未知'
        live = self.live(b)
        running = self.services.active([b]) if b else set()
        if b:
            def account(key):
                return (b[key + '_user'] or '未设用户名') + ('（已存密码）' if b[key + '_secret'] else '（未存密码）')
            values = dict(name=b['name'], serial=f"{b['port']} / {b['baudrate']}  ·  {state}" if b['port'] else '未绑定',
                          serial_user=account('serial'), telnet=(live.get('telnet') if state == READY else None) or '—',
                          web=b['web_url'] or '未设置', web_user=account('web'),
                          tftp=(f"已配置 {len(b['tftp_servers'])} 个，本窗口运行 {len(running)} 个"
                                if b['tftp_servers'] else '未配置'))
        else:
            values = {'name': '添加第一块开发板' if self.project_id else '新建项目以添加开发板'}
        for key, var in self.fields.items():
            var.set(values.get(key, '—'))
        editable = self.config_ok and self.project_id is not None
        enabled = dict(prompt=b and editable, start_tftp=b and len(running) < len(b['tftp_servers']), services=b,
                       refresh=True, release_all=self.known, add_group=self.config_ok, group_menu=editable,
                       add=editable, edit=b and editable and not running, delete=b and editable and not running,
                       claim=self.known and b and state == IDLE, copy_id=self.project_id, mcp=True,
                       release=self.known and live and state != CONFLICT,
                       telnet=self.known and state == READY and live.get('telnet'), copy=b,
                       group_copy=self.active_boards(),
                       group_claim=self.known and any(x['port'] for x in self.active_boards()), report=self.report)
        # A slow startup/refresh must not prevent offline catalog work. Serial
        # operations with a captured selection (handoff/claim/release) do lock it.
        local = {'copy_id', 'add_group', 'group_menu', 'add', 'edit', 'delete', 'prompt', 'services', 'start_tftp'}
        for key, widget in self.buttons.items():
            available = not self.busy or (key in local and not self.lock_profiles)
            widget.configure(state='normal' if enabled[key] and available else 'disabled')
        self.picker.configure(state='disabled' if self.lock_profiles else 'readonly')

    def project_menu(self):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='重命名项目', command=lambda: self.change_project('rename'))
        menu.add_command(label='删除项目', command=lambda: self.change_project('delete'))
        widget = self.buttons['group_menu']
        try:
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            menu.grab_release()

    def reload_catalog(self):
        try:
            latest = load_catalog()
        except (ValueError, OSError) as exc:
            self.config_ok = False
            self.details()
            messagebox.showerror('无法重新读取开发板档案', str(exc), parent=self.root)
            return False
        self.services.reconcile(latest.projects)
        self.catalog, self.config_ok = latest, True
        self.render()  # Preserve the current project and board IDs when they still exist.
        return True

    def persist(self, changed, project_id, board_id=None):
        if not self.config_ok or self.catalog is None:
            return False
        try:
            latest = save_catalog(changed, expected_revision=self.catalog.revision)
        except CatalogConflict:
            if self.reload_catalog():
                messagebox.showwarning('档案未保存', '其他窗口已修改档案。已加载最新版本，请核对后重新编辑。', parent=self.root)
            return False
        except (OSError, ValueError) as exc:
            messagebox.showerror('档案未保存', str(exc), parent=self.root)
            return False
        self.services.reconcile(latest.projects)
        self.catalog, self.project_id = latest, project_id
        self.render(board_id)
        self.status.set('档案已保存，串口连接未改变。')
        return True

    def change_project(self, action):
        old = self.active_project()
        if not self.config_ok or (action != 'new' and old is None):
            return
        if action == 'delete':
            if self.services.active(old.boards):
                messagebox.showinfo('项目仍在提供 TFTP 服务', '请先停止本窗口为此项目启动的服务。', parent=self.root)
                return
            if not messagebox.askyesno('删除项目', f'删除“{old.name}”及其所有开发板档案和登录资料？\n不会释放正在使用的串口。',
                                        default='no', parent=self.root):
                return
            projects = [p for p in self.projects if p.id != old.id]
            self.persist(projects, projects[0].id if projects else None)
            return
        value = simpledialog.askstring('项目组', '项目名称：', initialvalue=old.name if action == 'rename' else '',
                                       parent=self.root)
        if value is None:
            return
        try:
            name = project_name(value)
            if any(p.name == name and not (action == 'rename' and p.id == old.id) for p in self.projects):
                raise ValueError('该项目名称已存在。')
            if action == 'rename':
                project = Project(old.id, name, old.boards)
                projects = [project if p.id == old.id else p for p in self.projects]
            else:
                project = new_project(name)
                projects = [*self.projects, project]
        except ValueError as exc:
            messagebox.showerror('项目名称无效', str(exc), parent=self.root)
            return
        self.persist(projects, project.id)

    def save_members(self, project, members, board_id):
        updated = Project(project.id, project.name, members)
        return self.persist([updated if p.id == project.id else p for p in self.projects], project.id, board_id)

    def change_board(self, action):
        project = self.active_project()
        b = self.selected_board() if action != 'new' else new_board()
        if not b or project is None or not self.config_ok:
            return
        if action != 'new' and self.services.active([b]):
            messagebox.showinfo('开发板仍在提供 TFTP 服务', '请先停止本窗口为此板启动的服务，再编辑或删除档案。', parent=self.root)
            return
        members = list(project.boards)
        if action == 'delete':
            if not messagebox.askyesno('删除开发板', f"删除“{b['name']}”及其登录资料？串口不会被释放。",
                                        default='no', parent=self.root):
                return
            members = [x for x in members if x['id'] != b['id']]
            selection = None
        else:
            ports = sorted({x['port'] for x in self.inv['available'] + self.inv['open']})
            updated = edit_board(self.root, deepcopy(b), ports)
            if updated is None:
                return
            if action == 'new':
                members.append(updated)
            else:
                members = [updated if x['id'] == b['id'] else x for x in members]
            selection = updated['id']
        self.save_members(project, members, selection)

    def submit(self, job, pending, success, failure, *, lock_profiles=True):
        """One foreground operation at a time. Only this Tk-side poll updates widgets."""
        if self.busy or self.closed:
            return False
        self.busy, self.lock_profiles = True, lock_profiles
        if pending:
            self.status.set(pending)
        self.details()
        task = self.pool.submit(job)

        def poll():
            if not task.done():
                self.poll_id = self.root.after(50, poll)
                return
            self.poll_id = None
            self.busy = self.lock_profiles = False
            try:
                result = task.result()
            except Exception as exc:
                failure(exc)
            else:
                success(result)
            if not self.closed:
                self.details()
        self.poll_id = self.root.after(50, poll)
        return True

    def set_inventory(self, inventory):
        self.known = inventory is not None
        self.inv = inventory if self.known else EMPTY

    def run(self, job, pending, done=None, report=False, *, lock_profiles=True):
        def success(result):
            inventory, notice = result
            self.set_inventory(inventory)
            if report:
                self.report = notice
            self.status.set(notice.partition('\n')[0])
            if done:
                done()

        def failed(exc):
            self.set_inventory(None)
            if report:
                self.report = str(exc)
            self.status.set('串口状态未确认；可离线编辑，或在“MCP 服务/接入”中启动服务。' + str(exc).partition('\n')[0])
        self.submit(job, pending, success, failed, lock_profiles=lock_profiles)

    def refresh(self, *, start=False):
        if not start:
            self.reload_catalog()
        def inventory():
            # Only the initial window launch (or explicit Start in the panel) can spawn.
            return ensure_service() if start else inventory_snapshot(rpc)
        self.run(lambda: (inventory(), '状态已刷新。关闭窗口不会释放串口。'), '正在查询串口状态…',
                 self.report_tftp, lock_profiles=False)

    def show_mcp(self):
        if self.mcp_panel is None or not self.mcp_panel.winfo_exists():
            self.mcp_panel = ConsoleServicePanel(self, rpc, ensure_service)
        self.mcp_panel.present()

    def targets(self, group):
        b = self.selected_board()
        return list(self.active_boards()) if group else ([b] if b else [])

    def claim(self, group):
        boards = self.targets(group)
        if boards:
            self.run(lambda: claim_boards(rpc, boards), '正在按档案占用串口…', report=True)

    def start_board_services(self):
        board = self.selected_board()
        if not board or self.lock_profiles:
            return
        active = self.services.active([board])
        pending = [cfg for cfg in board['tftp_servers'] if (board['id'], cfg['name']) not in active]
        if not pending:
            return
        rows = [f"{c['name']}  {c['ip']}:{c['port']} / UDP\n    {c['directory']}" for c in pending]
        prompt = (f"为“{board['name']}”启动以下 {len(rows)} 个只读 TFTP 服务？\n\n" + '\n'.join(rows)
                  + '\n\nTFTP 没有身份验证或加密。能访问接口的设备均可下载共享目录中的文件。')
        if not messagebox.askyesno('启动本板 TFTP', prompt, default='no', parent=self.root):
            return
        results = []
        failures = 0
        for cfg in pending:
            try:
                self.services.start(board['id'], cfg)
                results.append(f"{cfg['name']}：已启动")
            except (OSError, ValueError, RuntimeError) as exc:
                failures += 1
                results.append(f"{cfg['name']}：启动失败，{exc}")
        summary = (f"{board['name']}：本次启动 {len(pending) - failures} 个，失败 {failures} 个。"
                   + ('请查看操作明细。' if failures else ''))
        self.report = '\n'.join([summary, *results])
        self.status.set(summary)
        self.details()

    def show_services(self):
        board = self.selected_board()
        if not board:
            return
        panel = ServicePanel(self.root, board, self.services)
        panel.wait_visibility()
        panel.grab_set()
        self.root.wait_window(panel)
        if self.root.winfo_exists():
            self.details()

    def edit_prompt(self):
        from .dialog_layout import show_centered
        board = self.selected_board()
        project = self.active_project()
        if not board or not project:
            return
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.transient(self.root)
        dialog.title(f"{board['name']} | 编辑 AI 提示")
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='MCP 查询此项目时会返回这段本板备注；手动复制本板或本组资料时也会附上。',
                  wraplength=540, justify='left').pack(anchor='w')
        text = ScrolledText(body, width=60, height=7, wrap='word', undo=True)
        text.pack(fill='both', expand=True, pady=8)
        text.insert('1.0', board['ai_prompt'])
        ttk.Label(body, text='不要在备注中填写密码。备注不会改变串口权限或共享状态。').pack(anchor='w')
        copy_login = tk.BooleanVar(dialog, board['copy_login'])
        ttk.Checkbutton(body, text='手动复制给 AI 时包含本板登录信息（用户名及明文密码）',
                        variable=copy_login).pack(anchor='w', pady=(6, 0))
        ttk.Label(body, text='此选项只影响手动复制；MCP 项目查询不会返回已保存的登录字段。').pack(anchor='w')
        controls = ttk.Frame(body)
        controls.pack(fill='x', pady=(8, 0))

        def restore():
            text.delete('1.0', 'end')
            text.insert('1.0', DEFAULT_AI_PROMPT)

        def save():
            updated = {**board, 'ai_prompt': text.get('1.0', 'end-1c'), 'copy_login': copy_login.get()}
            members = [updated if b['id'] == board['id'] else b for b in project.boards]
            if self.save_members(project, members, board['id']):
                dialog.destroy()
            elif self.active_project() != project or self.selected_board() != board:
                dialog.destroy()  # Another window changed the board while editing.

        ttk.Button(controls, text='恢复默认', command=restore).pack(side='left')
        ttk.Button(controls, text='保存提示', command=save).pack(side='right')
        ttk.Button(controls, text='取消', command=dialog.destroy).pack(side='right', padx=4)
        dialog.bind('<Escape>', lambda _: dialog.destroy())
        show_centered(dialog, self.root)
        dialog.wait_visibility()
        dialog.grab_set()
        text.focus_set()
        self.root.wait_window(dialog)

    def handoff(self, group):
        project, boards = self.active_project(), self.targets(group)
        if not project or not boards:
            return
        label = project.name if group else boards[0]['name']
        def snapshot():
            try:
                return inventory_snapshot(rpc), ''
            except Exception:
                return None, ''

        def copy():
            try:
                text = board_hint(project.name, boards, self.inv if self.known else None,
                                  running_services=self.services.active(boards))
            except ValueError as exc:
                self.status.set(str(exc))
                return
            has_password = any(b['copy_login'] and (b['serial_secret'] or b['web_secret']) for b in boards)
            self.clipboard(text, f'已复制“{label}”的资料。' + ('剪贴板含明文密码。' if has_password else '')
                           + ('' if self.known else '串口服务不可用，状态未确认。'))
        self.run(snapshot, '正在核对连接状态…', copy)

    def release(self, all_ports):
        board = self.selected_board()
        if not all_ports and not board:
            return
        def confirm():
            names = tuple(x['port'] for x in self.inv['open'] if all_ports or x['port'] == board['port'])
            if not names:
                self.status.set('没有需要释放的串口。')
                return
            if not all_ports and board_state(board, self.inv) == CONFLICT:
                self.status.set('当前连接与本板预设冲突，未释放。')
                return
            scope = '本服务的全部串口（不限于当前项目）' if all_ports else board['name'] + ' 的串口'
            if messagebox.askyesno('释放串口', f"释放{scope}：\n{'、'.join(names)}\n\n将停止采集。请确认其他会话不再需要这些连接。"
                                   '\n有 Telnet 客户端或活跃执行的端口不会强制释放。', parent=self.root, default='no'):
                self.run(lambda: release_ports(rpc, names), '正在释放已确认的串口…', report=True)
        self.run(lambda: (inventory_snapshot(rpc), ''), '正在核对释放范围…', confirm)

    def clipboard(self, text, notice):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.set(notice)

    def copy_telnet(self):
        b = self.selected_board()
        addr = self.live(b).get('telnet')
        if addr and self.known and board_state(b, self.inv) == READY:
            self.clipboard(addr, '已复制本机 Telnet 地址。')

    def view_report(self):
        from .dialog_layout import show_centered
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.transient(self.root)
        window.title('上次操作明细')
        text = ScrolledText(window, width=70, height=14, wrap='word', padx=12, pady=8)
        text.pack(fill='both', expand=True)
        text.insert('1.0', self.report)
        text.configure(state='disabled')
        show_centered(window, self.root)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
        if self.heartbeat_id is not None:
            self.root.after_cancel(self.heartbeat_id)
        try:
            self.services.close()
        finally:
            self.reporter.close()
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.root.destroy()


def main():
    root = tk.Tk()
    BoardConsole(root)
    root.mainloop()
