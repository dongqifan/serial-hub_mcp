"""A desktop register of development boards. All hardware I/O stays in the shared daemon."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from .board_editor import edit_board
from .board_operations import board_hint, board_state, claim_boards, release_ports, tftp_lines
from .boards import load_boards, new_board, save_boards
from .cli import rpc
from .gui_service import ensure_service
from .project_actions import CONFLICT, IDLE, READY
from .projects import project_name

# A bench register: quiet navigation, a prominent board name and three labelled connections.
DECK, INK, PAPER, SIGNAL, MUTED = '#e8f0f1', '#193b43', '#ffffff', '#08665e', '#45616a'
EMPTY = {'open': [], 'available': []}


class BoardConsole:
    def __init__(self, root):
        self.root = root
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.busy = False
        self.inv = EMPTY
        self.known = False
        self.report = ''
        self.poll_id = None
        self.config_ok = True
        try:
            self.catalog = load_boards()
        except (ValueError, OSError) as exc:
            self.catalog, self.config_ok = {}, False
            messagebox.showerror('无法读取开发板档案', str(exc), parent=root)
        self.project = tk.StringVar(root, next(iter(self.catalog), ''))
        self.status = tk.StringVar(root)
        self.fields = {k: tk.StringVar(root) for k in ('name', 'serial', 'telnet', 'serial_user', 'web', 'web_user', 'tftp')}
        self.buttons = {}
        root.title('开发板控制台')
        root.minsize(790, 460)
        root.configure(bg=DECK)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.build()
        self.render()
        self.refresh()

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
        body = tk.Frame(root, bg=DECK, padx=14, pady=10)
        body.pack(fill='both', expand=True)
        top = tk.Frame(body, bg=DECK)
        top.pack(fill='x', pady=(0, 8))
        tk.Label(top, text='项目组', bg=DECK, fg=INK).pack(side='left')
        self.picker = ttk.Combobox(top, width=18, state='readonly', textvariable=self.project)
        self.picker.pack(side='left', padx=6)
        self.picker.bind('<<ComboboxSelected>>', lambda _: self.render())
        self.button(top, 'add_group', '新建项目', lambda: self.change_project('new'))
        self.button(top, 'group_menu', '管理项目', self.project_menu)
        self.button(top, 'group_copy', '复制本组给 AI', lambda: self.handoff(True), 'right')
        self.button(top, 'group_claim', '占用本组串口', lambda: self.claim(True), 'right')

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
                                            ('web', 'Web 登录地址'), ('web_user', 'Web 账号'), ('tftp', '固件 TFTP'))):
            pad = (9, 0) if key in ('web', 'tftp') else (1, 0)
            tk.Label(grid, text=label, fg=MUTED, bg=PAPER, anchor='w').grid(row=row, column=0, sticky='nw', pady=pad)
            if key == 'tftp':
                self.tftp_display = ScrolledText(grid, height=3, width=36, wrap='word', relief='flat',
                                                 bg=PAPER, fg=INK, font='TkDefaultFont', state='disabled')
                self.tftp_display.grid(row=row, column=1, padx=(12, 2), sticky='ew', pady=pad)
                continue
            tk.Label(grid, textvariable=self.fields[key], fg=INK, bg=PAPER, anchor='w', justify='left',
                     wraplength=380).grid(row=row, column=1, padx=(12, 2), sticky='w', pady=pad)
        actions = tk.Frame(detail, bg=PAPER)
        actions.pack(fill='x', pady=(6, 0))
        self.button(actions, 'claim', '占用串口', lambda: self.claim(False))
        self.button(actions, 'release', '释放串口', lambda: self.release(False))
        self.button(actions, 'copy', '复制本板给 AI', lambda: self.handoff(False))
        extra = tk.Frame(detail, bg=PAPER)
        extra.pack(fill='x')
        self.button(extra, 'telnet', '复制 Telnet 地址', self.copy_telnet)
        self.button(extra, 'delete', '删除开发板', lambda: self.change_board('delete'), 'right')
        footer = tk.Frame(body, bg=DECK)
        footer.pack(fill='x', pady=(8, 0))
        self.button(footer, 'report', '操作明细', self.view_report, 'right')
        tk.Label(footer, textvariable=self.status, bg=DECK, fg=INK, anchor='w', justify='left',
                 wraplength=610).pack(side='left', fill='x', expand=True)

    def active_boards(self):
        return self.catalog.get(self.project.get(), [])

    def selected_board(self):
        ids = self.listing.selection()
        return next((b for b in self.active_boards() if ids and b['id'] == ids[0]), None)

    def render(self, selected=None):
        ids = self.listing.selection()
        self.picker.configure(values=list(self.catalog))
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
        if b:
            def account(key):
                return (b[key + '_user'] or '未设用户名') + ('（已存密码）' if b[key + '_secret'] else '（未存密码）')
            values = dict(name=b['name'], serial=f"{b['port']} / {b['baudrate']}  ·  {state}" if b['port'] else '未绑定',
                          serial_user=account('serial'), telnet=(live.get('telnet') if state == READY else None) or '—',
                          web=b['web_url'] or '未设置', web_user=account('web'), tftp='\n'.join(tftp_lines(b)) or '未设置')
        else:
            values = {'name': '添加第一块开发板' if self.project.get() else '新建项目以添加开发板'}
        for key, var in self.fields.items():
            var.set(values.get(key, '—'))
        self.tftp_display.configure(state='normal')
        self.tftp_display.delete('1.0', 'end')
        self.tftp_display.insert('1.0', self.fields['tftp'].get())
        self.tftp_display.configure(state='disabled')
        project = self.project.get() in self.catalog
        enabled = dict(refresh=True, release_all=self.known, add_group=self.config_ok, group_menu=project,
                       add=project, edit=b, delete=b, claim=self.known and b and state == IDLE,
                       release=self.known and live and state != CONFLICT,
                       telnet=self.known and state == READY and live.get('telnet'), copy=self.known and b,
                       group_copy=self.known and self.active_boards(),
                       group_claim=self.known and any(x['port'] for x in self.active_boards()), report=self.report)
        for key, widget in self.buttons.items():
            widget.configure(state='normal' if enabled[key] and not self.busy else 'disabled')
        self.picker.configure(state='disabled' if self.busy else 'readonly')

    def project_menu(self):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='重命名项目', command=lambda: self.change_project('rename'))
        menu.add_command(label='删除项目', command=lambda: self.change_project('delete'))
        widget = self.buttons['group_menu']
        try:
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            menu.grab_release()

    def persist(self, changed, project, board_id=None):
        try:
            latest = load_boards()
            if latest != self.catalog:
                self.catalog = latest
                self.project.set(next(iter(latest), '') if project not in latest else project)
                self.render()
                raise ValueError('档案已在其他窗口修改。已更新视图，请重新编辑。')
            save_boards(changed)
        except (OSError, ValueError) as exc:
            messagebox.showerror('档案未保存', str(exc), parent=self.root)
            return
        self.catalog = changed
        self.project.set(project)
        self.render(board_id)
        self.status.set('档案已保存，串口连接未改变。')

    def change_project(self, action):
        data = dict(self.catalog)
        old = self.project.get()
        if action == 'delete':
            if not messagebox.askyesno('删除项目', f'删除“{old}”及其所有开发板档案和登录资料？\n不会释放正在使用的串口。',
                                        default='no', parent=self.root):
                return
            del data[old]
            choice = next(iter(data), '')
        else:
            value = simpledialog.askstring('项目组', '项目名称：', initialvalue=old if action == 'rename' else '', parent=self.root)
            if value is None:
                return
            try:
                choice = project_name(value)
                if choice in data and not (action == 'rename' and old == choice):
                    raise ValueError('该项目名称已存在。')
            except ValueError as exc:
                messagebox.showerror('项目名称无效', str(exc), parent=self.root)
                return
            data[choice] = data.pop(old) if action == 'rename' else []
        self.persist(data, choice)

    def change_board(self, action):
        p = self.project.get()
        b = self.selected_board() if action != 'new' else new_board()
        if not b or p not in self.catalog:
            return
        members = list(self.catalog[p])
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
        self.persist({**self.catalog, p: members}, p, selection)

    def run(self, job, pending, done=None, report=False):
        if self.busy:
            return
        self.busy = True
        self.status.set(pending)
        self.details()
        task = self.pool.submit(job)

        def poll():
            if not task.done():
                self.poll_id = self.root.after(50, poll)
                return
            self.poll_id = None
            self.busy = False
            try:
                self.inv, notice = task.result()
                self.known = True
                if report:
                    self.report = notice
                self.status.set(notice.partition('\n')[0])
                self.details()
                if done:
                    done()
            except Exception as exc:
                self.known = False
                self.inv = EMPTY
                if report:
                    self.report = str(exc)
                self.status.set('无法确认串口状态，请刷新。' + str(exc).partition('\n')[0])
                self.details()
        self.poll_id = self.root.after(50, poll)

    def refresh(self):
        self.run(lambda: (ensure_service(), '状态已刷新。关闭窗口不会释放串口。'), '正在查询串口状态…')

    def targets(self, group):
        b = self.selected_board()
        return list(self.active_boards()) if group else ([b] if b else [])

    def claim(self, group):
        boards = self.targets(group)
        if boards:
            self.run(lambda: claim_boards(rpc, boards), '正在按档案占用串口…', report=True)

    def handoff(self, group):
        project, boards = self.project.get(), self.targets(group)
        if not boards:
            return
        label = project if group else boards[0]['name']
        def copy():
            self.clipboard(board_hint(project, boards, self.inv), f'已复制“{label}”的资料，不含密码。')
        self.run(lambda: (rpc('list_ports', {}), ''), '正在核对连接状态…', copy)

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
        self.run(lambda: (rpc('list_ports', {}), ''), '正在核对释放范围…', confirm)

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
        window = tk.Toplevel(self.root)
        window.title('上次串口操作明细')
        text = ScrolledText(window, width=70, height=14, wrap='word', padx=12, pady=8)
        text.pack(fill='both', expand=True)
        text.insert('1.0', self.report)
        text.configure(state='disabled')

    def close(self):
        if self.poll_id:
            self.root.after_cancel(self.poll_id)
        self.pool.shutdown(wait=False)
        self.root.destroy()


def main():
    root = tk.Tk()
    BoardConsole(root)
    root.mainloop()
