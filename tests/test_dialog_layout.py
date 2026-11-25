import pytest

from serial_hub_mcp.dialog_layout import _bounds, show_centered


def test_center_on_negative_coordinate_monitor_and_clamp_to_work_area():
    area = (-1600, -300, 0, 600)
    assert _bounds((-1400, -250, 800, 500), (400, 200), area) == (400, 200, -1200, -100)
    w, h, x, y = _bounds((-300, 400, 700, 500), (600, 300), area)
    assert x + w <= area[2] and y + h <= area[3]
    w, h, x, y = _bounds((-1700, -600, 300, 100), (2000, 3000), area)
    assert (x, y) == area[:2] and w <= 1600 and h <= 900


def test_modal_first_visibility_is_centered_on_moved_owner():
    tk = pytest.importorskip('tkinter')
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'No Tk display: {exc}')
    try:
        root.geometry('340x220+160+100')
        root.update()
        panel = tk.Toplevel(root)
        panel.withdraw()
        panel.transient(root)
        tk.Label(panel, text='Board details', padx=20, pady=16).pack()
        show_centered(panel, root)
        assert not panel.winfo_viewable()
        timeout = root.after(3000, panel.destroy)
        panel.wait_visibility()
        root.after_cancel(timeout)
        panel.grab_set()
        assert panel.grab_current() == panel
        # Native decorations shift the client origin slightly on Windows.
        assert abs(panel.winfo_rootx() + panel.winfo_width()/2 - root.winfo_rootx() - root.winfo_width()/2) < 36
        assert abs(panel.winfo_rooty() + panel.winfo_height()/2 - root.winfo_rooty() - root.winfo_height()/2) < 48
    finally:
        root.destroy()
