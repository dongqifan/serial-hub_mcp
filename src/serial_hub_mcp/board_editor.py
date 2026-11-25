"""Edit one local board profile. No hardware, browser or firmware actions here."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from . import board_secrets, boards
from .dialog_layout import show_centered


class PasswordField(ttk.Frame):
    """An empty replacement preserves the ciphertext; never decrypt just to edit a board."""
    def __init__(self, parent, stored):
        super().__init__(parent)
        self.stored = stored
        self.cleared = False
        self.timer = None
        self.value = tk.StringVar(self)
        self.note = tk.StringVar(self)
        self.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(self, textvariable=self.value, show='*', exportselection=False)
        self.entry.grid(row=0, column=0, sticky='ew')
        for action in ('<<Copy>>', '<<Cut>>'):
            self.entry.bind(action, lambda _: 'break')
        self.show_button = ttk.Button(self, text='查看', width=6, command=self.show)
        self.show_button.grid(row=0, column=1, padx=(4, 0))
        ttk.Button(self, text='清除', width=6, command=self.clear).grid(row=0, column=2, padx=(4, 0))
        ttk.Label(self, textvariable=self.note, wraplength=340).grid(row=1, column=0, columnspan=3,
                                                                    sticky='w', pady=(4, 0))
        self.value.trace_add('write', lambda *_: self.hide())
        self.hide()

    def hide(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        self.show_button.configure(text='查看')
        if self.value.get():
            self.note.set('保存后使用新密码。')
        elif self.cleared:
            self.note.set('保存后清除密码。')
        elif self.stored:
            self.note.set('已保存密码；留空保留原值。')
        else:
            self.note.set('未设置密码。')

    def show(self):
        if self.timer is not None:
            self.hide()
            return
        text = self.value.get()
        if not text and self.stored and not self.cleared:
            try:
                text = board_secrets.reveal(self.stored)
            except Exception:
                self.note.set('此账户无法解密。留空可保留原密码，也可以重新输入。')
                return
        if text:
            self.note.set(text)
            self.show_button.configure(text='隐藏')
            self.timer = self.after(10000, self.hide)

    def clear(self):
        self.cleared = True
        self.value.set('')

    def protected_value(self):
        if self.value.get():
            return board_secrets.protect(self.value.get())
        return '' if self.cleared else self.stored

    def destroy(self):
        self.value.set('')
        self.note.set('')
        super().destroy()


class TftpServers(ttk.Frame):
    """Edit per-board local services without starting any listeners."""
    columns = (('name', '服务名称', 13), ('ip', '绑定接口 IPv4', 16), ('port', 'UDP 端口', 8),
               ('directory', '只读固件目录', 23))
    defaults = {'name': '', 'ip': '127.0.0.1', 'port': 69, 'directory': ''}
    add_text, list_name, wide_column = '添加本机服务', '本机 TFTP 配置', 3
    sheet_width = 655

    def __init__(self, parent, targets):
        super().__init__(parent)
        self.rows = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, width=self.sheet_width, height=140, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        bar.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=bar.set)
        self.sheet = ttk.Frame(self.canvas)
        sheet_id = self.canvas.create_window(0, 0, window=self.sheet, anchor='nw')
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(sheet_id, width=e.width))
        self.sheet.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.sheet.columnconfigure(self.wide_column, weight=1)
        for i, (_, label, _) in enumerate(self.columns):
            ttk.Label(self.sheet, text=label).grid(row=0, column=i, sticky='w', padx=3)
        for target in targets:
            self.add(target)
        ttk.Button(self, text=self.add_text, command=self.add).grid(row=1, column=0, sticky='w', pady=(8, 0))

    def add(self, target=None):
        data = target or self.defaults
        index = self.sheet.grid_size()[1]
        entries = {}
        for col, (key, _, width) in enumerate(self.columns):
            var = tk.StringVar(self, str(data[key]))
            entry = ttk.Entry(self.sheet, width=width, textvariable=var)
            entry.grid(row=index, column=col, sticky='ew', padx=3, pady=2)
            entries[key] = (var, entry)
            if key == 'directory':
                browse = ttk.Button(self.sheet, text='选择…', width=6, command=lambda v=var: self.browse(v))
                browse.grid(row=index, column=len(self.columns), padx=3)
                browse.bind('<FocusIn>', lambda e: self.see(e.widget))
            entry.bind('<FocusIn>', lambda e: self.see(e.widget))
        remove = ttk.Button(self.sheet, text='移除', width=5, command=lambda: self.remove(row))
        remove.grid(row=index, column=len(self.columns) + ('directory' in entries), padx=3)
        remove.bind('<FocusIn>', lambda e: self.see(e.widget))
        row = entries, remove
        self.rows.append(row)
        if target is None:
            self.update_idletasks()
            self.canvas.yview_moveto(1)
            next(iter(entries.values()))[1].focus_set()
        return row

    def browse(self, var):
        folder = filedialog.askdirectory(parent=self.winfo_toplevel(), title='选择专用固件目录', mustexist=True)
        if folder:
            var.set(folder)

    def see(self, widget):
        top = self.canvas.canvasy(0)
        y = widget.winfo_y()
        height = self.canvas.winfo_height()
        if y < top or y + widget.winfo_height() > top + height:
            self.canvas.yview_moveto(y / max(1, self.sheet.winfo_height()))

    def remove(self, row):
        self.rows.remove(row)
        _, button = row
        for widget in self.sheet.grid_slaves(row=int(button.grid_info()['row'])):
            widget.destroy()

    def values(self):
        result = []
        for i, (entries, _) in enumerate(self.rows, start=1):
            data = {key: var.get() for key, (var, _) in entries.items()}
            try:
                data['port'] = int(data['port'])
            except ValueError:
                raise ValueError(f'第 {i} 条{self.list_name}：UDP 端口必须填写整数。') from None
            result.append(data)
        return result


class BoardEditor(tk.Toplevel):
    def __init__(self, parent, board, available):
        super().__init__(parent)
        self.withdraw()
        self.transient(parent)
        self.title('开发板资料')
        self.board = dict(board)
        self.result = None
        self.values = {}
        self.passwords = {}
        self.error = tk.StringVar(self)
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1)
        name_entry = self.field(body, 0, '开发板名称', 'name')
        notebook = ttk.Notebook(body)
        notebook.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(12, 4))
        body.rowconfigure(1, weight=1)
        tabs = []
        for title in ('串口登录', 'Web 登录', '本机 TFTP 服务'):
            page = ttk.Frame(notebook, padding=12)
            page.columnconfigure(1, weight=1)
            notebook.add(page, text=title)
            tabs.append(page)
        notebook.enable_traversal()
        serial, web, local = tabs
        ports = sorted(set(available), key=lambda n: (len(n), n))
        self.field(serial, 0, '串口（可选）', 'port', values=ports)
        self.field(serial, 1, '波特率', 'baudrate', values=(9600, 19200, 38400, 57600, 115200, 230400, 921600))
        self.field(serial, 2, '板端用户名', 'serial_user')
        self.password(serial, 3, '板端密码', 'serial_secret')
        self.field(web, 0, '登录地址', 'web_url')
        self.field(web, 1, '用户名', 'web_user')
        self.password(web, 2, '密码', 'web_secret')
        ttk.Label(web, text='填写 http:// 或 https:// 地址；账号密码请单独填写。').grid(
            row=3, column=0, columnspan=2, sticky='w', pady=6)
        self.servers = TftpServers(local, board['tftp_servers'])
        self.servers.grid(row=0, column=0, columnspan=2, sticky='nsew')
        local.rowconfigure(0, weight=1)
        ttk.Label(local, text='TFTP 无身份验证，只选择专用固件目录。127.0.0.1 仅供本机测试；'
                  '供开发板下载时请填写直连网卡的 IPv4 地址。保存后仍需手动启动。', wraplength=610).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=6)
        notebook.bind('<<NotebookTabChanged>>', lambda _: [pw.hide() for pw in self.passwords.values()])
        ttk.Label(body, textvariable=self.error, foreground='#8d3432', wraplength=440).grid(
            row=2, column=0, columnspan=2, sticky='w')
        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        ttk.Button(footer, text='保存开发板', command=self.save).pack(side='right')
        ttk.Button(footer, text='取消', command=self.destroy).pack(side='right', padx=5)
        self.bind('<Escape>', lambda _: self.destroy())
        show_centered(self, parent)
        name_entry.focus_set()

    def field(self, page, row, label, key, values=None):
        var = self.values[key] = tk.StringVar(self, str(self.board.get(key, '')))
        ttk.Label(page, text=label).grid(row=row, column=0, sticky='w', padx=(0, 10), pady=4)
        field = (ttk.Combobox(page, width=30, textvariable=var, values=values) if values is not None else
                 ttk.Entry(page, width=40, textvariable=var))
        field.grid(row=row, column=1, sticky='ew', pady=4)
        return field

    def password(self, page, row, label, key):
        ttk.Label(page, text=label).grid(row=row, column=0, sticky='nw', padx=(0, 10), pady=4)
        field = self.passwords[key] = PasswordField(page, self.board[key])
        field.grid(row=row, column=1, sticky='ew', pady=4)

    def save(self):
        board = {**self.board, **{k: v.get() for k, v in self.values.items()}}
        try:
            board['baudrate'] = int(board['baudrate'])
        except ValueError:
            self.error.set('波特率必须填写整数。')
            return
        try:
            board['tftp_servers'] = self.servers.values()
            board = boards.normalize_board(board)
        except ValueError as exc:
            # normalize_board uses fixed field-specific messages, never input values.
            self.error.set(str(exc))
            return
        try:
            for key, field in self.passwords.items():
                board[key] = field.protected_value()
        except Exception:
            self.error.set('无法保护密码。请在 Windows 当前账户下重试；没有保存任何更改。')
            return
        self.result = board
        self.destroy()


def edit_board(parent, board, available) -> dict | None:
    dialog = BoardEditor(parent, board, available)
    dialog.wait_visibility()
    dialog.grab_set()
    parent.wait_window(dialog)
    return dialog.result
