"""Edit local project presets without opening ports or contacting the daemon."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .projects import ALL_PORTS, member, project_name


class ProjectEditor(tk.Toplevel):
    def __init__(self, parent, projects, current, available):
        super().__init__(parent)
        self.transient(parent)
        self.title('新建项目组' if current is None else '编辑项目组')
        self.result = None
        self.projects, self.current = projects, current
        self.available = sorted(set(available), key=lambda p: (len(p), p))
        self.rows = []
        self.name = tk.StringVar(self, current or '')
        self.error = tk.StringVar(self)
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        heading = ttk.Frame(body)
        heading.pack(fill='x')
        ttk.Label(heading, text='项目组名称').pack(side='left', padx=(0, 8))
        name_entry = ttk.Entry(heading, textvariable=self.name)
        name_entry.pack(side='left', fill='x', expand=True)
        ttk.Label(body, text='串口可从列表选择，也可手动填写。保存配置不会占用或释放串口。').pack(anchor='w', pady=10)

        viewport = ttk.Frame(body)
        viewport.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(viewport, width=480, height=185, borderwidth=0, highlightthickness=0)
        bar = ttk.Scrollbar(viewport, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.sheet = ttk.Frame(self.canvas)
        window = self.canvas.create_window(0, 0, window=self.sheet, anchor='nw')
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.sheet.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.sheet.columnconfigure(2, weight=1)
        for i, label in enumerate(('串口', '波特率', '设备角色（可选）')):
            ttk.Label(self.sheet, text=label).grid(row=0, column=i, sticky='w', padx=3, pady=4)
        for item in projects.get(current, []):
            self.add(item)
        if current is None:
            self.add()
        ttk.Button(body, text='添加串口', command=self.append_row).pack(anchor='w', pady=(7, 0))
        ttk.Label(body, textvariable=self.error, foreground='#8d3432', wraplength=460).pack(anchor='w', pady=(5, 2))
        footer = ttk.Frame(body)
        footer.pack(fill='x', pady=(6, 0))
        if current is not None:
            ttk.Button(footer, text='删除项目组', command=self.delete).pack(side='left')
        ttk.Button(footer, text='保存项目组', command=self.save).pack(side='right')
        ttk.Button(footer, text='取消', command=self.destroy).pack(side='right', padx=6)
        self.bind('<Escape>', lambda _: self.destroy())
        name_entry.focus_set()

    def add(self, item=None):
        item = item or {}
        port = tk.StringVar(self, item.get('port', ''))
        baud = tk.StringVar(self, str(item.get('baudrate', 115200)))
        role = tk.StringVar(self, item.get('role', ''))
        row = max((s[-1] for s in self.rows), default=0) + 1
        inputs = (ttk.Combobox(self.sheet, textvariable=port, width=10, values=self.available),
                  ttk.Combobox(self.sheet, textvariable=baud, width=10,
                               values=(9600, 19200, 38400, 57600, 115200, 230400, 921600)),
                  ttk.Entry(self.sheet, textvariable=role, width=18),
                  ttk.Button(self.sheet, text='移除', width=5, command=lambda: self.remove(record)))
        record = (port, baud, role, inputs, row)
        self.rows.append(record)
        for col, widget in enumerate(inputs):
            widget.grid(row=row, column=col, sticky='ew', padx=3, pady=3)
        return inputs[0]

    def append_row(self):
        field = self.add()
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1)
        field.focus_set()

    def remove(self, record):
        for widget in record[3]:
            widget.destroy()
        self.rows.remove(record)

    def save(self):
        try:
            name = project_name(self.name.get())
            if name != self.current and name in self.projects:
                raise ValueError('这个项目组名称已存在。')
            entries = []
            seen = set()
            for port, baud, role, _, _ in self.rows:
                if not port.get().strip() and not role.get().strip() and baud.get().strip() == '115200':
                    continue  # A completely blank placeholder isn't a member.
                try:
                    rate = int(baud.get())
                except ValueError:
                    raise ValueError(f'{port.get()} 的波特率必须是整数。') from None
                entry = member({'port': port.get(), 'baudrate': rate, 'role': role.get()})
                if entry['port'] in seen:
                    raise ValueError(f"{entry['port']} 已在本组中，每组只能添加一次。")
                seen.add(entry['port'])
                entries.append(entry)
        except ValueError as exc:
            self.error.set(str(exc))
            return
        if self.current is not None and name != self.current and not messagebox.askyesno(
                '重命名项目组', f'将“{self.current}”改名为“{name}”？', parent=self):
            return
        updated = {k: v for k, v in self.projects.items() if k != self.current}
        self.result = ({**updated, name: entries}, name)
        self.destroy()

    def delete(self):
        if messagebox.askyesno('删除项目组', f'删除“{self.current}”的项目配置？\n'
                               '已占用的串口不会释放，其他项目组不受影响。', parent=self):
            self.result = ({k: v for k, v in self.projects.items() if k != self.current}, ALL_PORTS)
            self.destroy()


def edit_project(parent, projects, current, available) -> tuple[dict, str] | None:
    dialog = ProjectEditor(parent, projects, current, available)
    dialog.wait_visibility()
    dialog.grab_set()
    parent.wait_window(dialog)
    return dialog.result
