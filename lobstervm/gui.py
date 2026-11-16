#!/usr/bin/env python3
"""Graphical VM manager for lobstervm (tkinter).

Exposes:
    main(store=None) -> int
    launch() -> int  # convenience for CLI
"""

import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from typing import Optional, Callable, Any

from .config import VmConfig, VmStore, VmError, MACHINE_BY_MEMORY_GB, DISPLAY_BACKENDS
from .machine import Machine

# ── Constants ────────────────────────────────────────────────────────

REFRESH_INTERVAL_MS = 3000
SERIAL_TAIL_LINES = 100
SERIAL_REFRESH_MS = 500

# ── Pure helper functions (testable without Tk) ───────────────────────


def format_vm_row(config: VmConfig, state: str) -> str:
    """Format a single VM row for the listbox."""
    return f"{config.name:<20} {state:<8} {config.memory_gb}GB {config.resolution} display={config.display}"


def serial_tail(path: Path, n: int = SERIAL_TAIL_LINES) -> str:
    """Return the last n lines of a serial log file."""
    try:
        data = path.read_bytes()
        lines = data.decode("utf-8", "replace").splitlines()
        return "\n".join(lines[-n:])
    except FileNotFoundError:
        return "(serial log not found)"
    except OSError as e:
        return f"(error reading serial log: {e})"


def validate_new_vm_params(name: str, memory_gb: int, resolution: str, disk_size_gb: int) -> Optional[str]:
    """Validate new VM parameters using config.py logic. Returns error string or None."""
    # Reuse VmConfig validation by creating a temporary config
    try:
        config = VmConfig(name=name, memory_gb=memory_gb, resolution=resolution)
        config.validate()
    except VmError as e:
        return str(e)
    if disk_size_gb < 1 or disk_size_gb > 1024:
        return "disk size must be between 1 and 1024 GB"
    return None


# ── Background task runner ───────────────────────────────────────────


class AsyncRunner:
    """Run a callable in a background thread and marshal result to Tk mainloop."""

    def __init__(self, root: tk.Tk):
        self.root = root

    def run(self, func: Callable[[], Any], on_done: Callable[[Any], None],
            on_error: Callable[[Exception], None]):
        def worker():
            try:
                result = func()
                self.root.after(0, lambda: on_done(result))
            except Exception as error:  # noqa: BLE001 — forwarded to the UI thread
                self.root.after(0, lambda err=error: on_error(err))

        threading.Thread(target=worker, daemon=True).start()


# ── Dialogs ──────────────────────────────────────────────────────────


class NewVmDialog:
    """Dialog to create a new VM."""

    def __init__(self, parent: tk.Tk, store: VmStore):
        self.store = store
        self.result = None

        self.top = tk.Toplevel(parent)
        self.top.title("New VM")
        self.top.transient(parent)
        self.top.grab_set()
        self.top.resizable(False, False)

        # Center on parent
        self.top.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - 200
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - 150
        self.top.geometry(f"+{x}+{y}")

        # Form variables
        self.name_var = tk.StringVar()
        self.memory_var = tk.StringVar(value="4")
        self.resolution_var = tk.StringVar(value="1920x1080")
        self.disk_size_var = tk.StringVar(value="20")

        # Layout
        frame = ttk.Frame(self.top, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Name:").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.name_var, width=30).grid(row=0, column=1, pady=4)

        ttk.Label(frame, text="Memory (GB):").grid(row=1, column=0, sticky=tk.W, pady=4)
        memory_combo = ttk.Combobox(frame, textvariable=self.memory_var,
                                     values=[str(m) for m in sorted(MACHINE_BY_MEMORY_GB)],
                                     state="readonly", width=27)
        memory_combo.grid(row=1, column=1, pady=4)

        ttk.Label(frame, text="Resolution:").grid(row=2, column=0, sticky=tk.W, pady=4)
        res_combo = ttk.Combobox(frame, textvariable=self.resolution_var,
                                  values=["1920x1080", "1280x720", "1600x900", "2560x1440"],
                                  width=27)
        res_combo.grid(row=2, column=1, pady=4)

        ttk.Label(frame, text="Disk size (GB):").grid(row=3, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.disk_size_var, width=30).grid(row=3, column=1, pady=4)

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(15, 0))
        ttk.Button(btn_frame, text="Create", command=self._on_create).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side=tk.LEFT, padx=5)

        self.name_var.trace_add("write", lambda *_: self._validate())
        self.memory_var.trace_add("write", lambda *_: self._validate())
        self.resolution_var.trace_add("write", lambda *_: self._validate())
        self.disk_size_var.trace_add("write", lambda *_: self._validate())

        self.status_label = ttk.Label(frame, text="", foreground="red")
        self.status_label.grid(row=5, column=0, columnspan=2, pady=(5, 0))

        self.create_btn = btn_frame.winfo_children()[0]
        self._validate()

        self.top.wait_window()

    def _validate(self):
        err = validate_new_vm_params(
            self.name_var.get().strip(),
            int(self.memory_var.get() or 0),
            self.resolution_var.get().strip(),
            int(self.disk_size_var.get() or 0),
        )
        if err:
            self.status_label.config(text=err)
            self.create_btn.config(state=tk.DISABLED)
        else:
            self.status_label.config(text="")
            self.create_btn.config(state=tk.NORMAL)

    def _on_create(self):
        name = self.name_var.get().strip()
        try:
            config = VmConfig(
                name=name,
                memory_gb=int(self.memory_var.get()),
                resolution=self.resolution_var.get().strip(),
            )
            config.validate()
            self.store.create(config)
            # Resize disk to requested size (qemu-img resize)
            disk_path = self.store.disk_path(name)
            import subprocess
            subprocess.run(["qemu-img", "resize", str(disk_path), f"{self.disk_size_var.get()}G"],
                          check=True, capture_output=True)
            self.result = name
            self.top.destroy()
        except VmError as e:
            messagebox.showerror("Error", str(e), parent=self.top)
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Error", f"Failed to resize disk: {e.stderr.decode()}", parent=self.top)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create VM: {e}", parent=self.top)

    def _on_cancel(self):
        self.top.destroy()


class SettingsDialog:
    """Dialog to modify an existing VM's settings."""

    def __init__(self, parent: tk.Tk, store: VmStore, config: VmConfig):
        self.store = store
        self.config = config
        self.result = False

        self.top = tk.Toplevel(parent)
        self.top.title(f"Settings — {config.name}")
        self.top.transient(parent)
        self.top.grab_set()
        self.top.resizable(False, False)

        self.top.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - 200
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - 200
        self.top.geometry(f"+{x}+{y}")

        # Form variables (pre-filled)
        self.memory_var = tk.StringVar(value=str(config.memory_gb))
        self.resolution_var = tk.StringVar(value=config.resolution)
        self.display_var = tk.StringVar(value=config.display)
        self.audio_var = tk.BooleanVar(value=config.audio)
        self.shared_var = tk.StringVar(value=config.shared_folder)
        self.kernel_var = tk.StringVar(value=config.kernel)
        self.desc_var = tk.StringVar(value=config.description)

        frame = ttk.Frame(self.top, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        row = 0
        ttk.Label(frame, text="Memory (GB):").grid(row=row, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(frame, textvariable=self.memory_var,
                      values=[str(m) for m in sorted(MACHINE_BY_MEMORY_GB)],
                      state="readonly", width=27).grid(row=row, column=1, pady=4)
        row += 1

        ttk.Label(frame, text="Resolution:").grid(row=row, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(frame, textvariable=self.resolution_var,
                      values=["1920x1080", "1280x720", "1600x900", "2560x1440"],
                      width=27).grid(row=row, column=1, pady=4)
        row += 1

        ttk.Label(frame, text="Display:").grid(row=row, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(frame, textvariable=self.display_var,
                      values=list(DISPLAY_BACKENDS), state="readonly", width=27).grid(row=row, column=1, pady=4)
        row += 1

        ttk.Checkbutton(frame, text="Audio", variable=self.audio_var).grid(row=row, column=1, sticky=tk.W, pady=4)
        row += 1

        ttk.Label(frame, text="Shared folder:").grid(row=row, column=0, sticky=tk.W, pady=4)
        shared_frame = ttk.Frame(frame)
        shared_frame.grid(row=row, column=1, pady=4, sticky=tk.EW)
        ttk.Entry(shared_frame, textvariable=self.shared_var, width=22).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(shared_frame, text="Browse...", width=10,
                   command=lambda: self._browse(self.shared_var)).pack(side=tk.LEFT, padx=(5, 0))
        row += 1

        ttk.Label(frame, text="Kernel:").grid(row=row, column=0, sticky=tk.W, pady=4)
        kernel_frame = ttk.Frame(frame)
        kernel_frame.grid(row=row, column=1, pady=4, sticky=tk.EW)
        ttk.Entry(kernel_frame, textvariable=self.kernel_var, width=22).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(kernel_frame, text="Browse...", width=10,
                   command=lambda: self._browse(self.kernel_var, filetypes=[("Image files", "*.img"), ("All files", "*")])).pack(side=tk.LEFT, padx=(5, 0))
        row += 1

        ttk.Label(frame, text="Description:").grid(row=row, column=0, sticky=tk.NW, pady=4)
        ttk.Entry(frame, textvariable=self.desc_var, width=30).grid(row=row, column=1, pady=4, sticky=tk.EW)
        row += 1

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(15, 0))
        ttk.Button(btn_frame, text="Save", command=self._on_save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side=tk.LEFT, padx=5)

        self.status_label = ttk.Label(frame, text="", foreground="red")
        self.status_label.grid(row=row+1, column=0, columnspan=2, pady=(5, 0))

        self.top.wait_window()

    def _browse(self, var: tk.StringVar, filetypes=None):
        if filetypes:
            path = filedialog.askopenfilename(parent=self.top, filetypes=filetypes)
        else:
            path = filedialog.askdirectory(parent=self.top)
        if path:
            var.set(path)

    def _on_save(self):
        try:
            self.config.memory_gb = int(self.memory_var.get())
            self.config.resolution = self.resolution_var.get().strip()
            self.config.display = self.display_var.get().strip()
            self.config.audio = self.audio_var.get()
            self.config.shared_folder = self.shared_var.get().strip()
            self.config.kernel = self.kernel_var.get().strip()
            self.config.description = self.desc_var.get().strip()
            self.config.validate()
            self.store.save(self.config)
            self.result = True
            self.top.destroy()
        except VmError as e:
            self.status_label.config(text=str(e))
        except Exception as e:
            self.status_label.config(text=f"Failed to save: {e}")

    def _on_cancel(self):
        self.top.destroy()


class SnapshotDialog:
    """Dialog to manage snapshots for a VM."""

    def __init__(self, parent: tk.Tk, machine: Machine, store: VmStore, refresh_callback: Callable):
        self.machine = machine
        self.store = store
        self.refresh_callback = refresh_callback

        self.top = tk.Toplevel(parent)
        self.top.title(f"Snapshots — {machine.name}")
        self.top.transient(parent)
        self.top.grab_set()
        self.top.geometry("400x300")

        self.top.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - 200
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - 150
        self.top.geometry(f"+{x}+{y}")

        frame = ttk.Frame(self.top, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # Snapshot list
        self.listbox = tk.Listbox(frame, height=10)
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self._reload()

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Create...", command=self._on_create).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Restore", command=self._on_restore).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Delete", command=self._on_delete).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Close", command=self.top.destroy).pack(side=tk.RIGHT, padx=2)

        self.top.wait_window()

    def _reload(self):
        self.listbox.delete(0, tk.END)
        try:
            tags = self.machine.snapshot_list()
            for tag in tags:
                self.listbox.insert(tk.END, tag)
        except VmError as e:
            messagebox.showerror("Error", str(e), parent=self.top)

    def _on_create(self):
        name = tk.simpledialog.askstring("New Snapshot", "Tag name:", parent=self.top)
        if name:
            try:
                self.machine.snapshot_take(name.strip())
                self._reload()
                self.refresh_callback()
            except VmError as e:
                messagebox.showerror("Error", str(e), parent=self.top)

    def _on_restore(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        tag = self.listbox.get(sel[0])
        if messagebox.askyesno("Restore Snapshot", f"Restore snapshot '{tag}'? This will revert the VM state.", parent=self.top):
            try:
                self.machine.snapshot_restore(tag)
                self._reload()
                self.refresh_callback()
            except VmError as e:
                messagebox.showerror("Error", str(e), parent=self.top)

    def _on_delete(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        tag = self.listbox.get(sel[0])
        if messagebox.askyesno("Delete Snapshot", f"Delete snapshot '{tag}'?", parent=self.top):
            try:
                self.machine.snapshot_delete(tag)
                self._reload()
                self.refresh_callback()
            except VmError as e:
                messagebox.showerror("Error", str(e), parent=self.top)


# ── Main Application ─────────────────────────────────────────────────


class LobsterVmManager:
    def __init__(self, root: tk.Tk, store: Optional[VmStore] = None):
        self.root = root
        self.store = store or VmStore()
        self.runner = AsyncRunner(root)

        self.selected_vm_name: Optional[str] = None
        self.serial_offset = 0
        self._serial_after_id: Optional[str] = None
        self._refresh_after_id: Optional[str] = None

        self._build_ui()
        self._start_auto_refresh()
        self.refresh_vm_list()

    def _build_ui(self):
        self.root.title("LobsterVM Manager")
        self.root.geometry("1000x650")
        self.root.minsize(800, 500)

        # Menu bar
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New VM...", command=self._on_new_vm, accelerator="Ctrl+N")
        file_menu.add_separator()
        file_menu.add_command(label="Refresh", command=self.refresh_vm_list, accelerator="F5")
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.root.quit, accelerator="Ctrl+Q")
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)

        # Keyboard shortcuts
        self.root.bind("<Control-n>", lambda e: self._on_new_vm())
        self.root.bind("<F5>", lambda e: self.refresh_vm_list())
        self.root.bind("<Control-q>", lambda e: self.root.quit())

        # Main paned window
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ── Left pane: VM list ──────────────────────────────────────
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)

        # Toolbar
        toolbar = ttk.Frame(left_frame)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(toolbar, text="⟳ Refresh", command=self.refresh_vm_list).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="+ New VM", command=self._on_new_vm).pack(side=tk.LEFT, padx=5)

        # VM Listbox with scrollbar
        list_frame = ttk.Frame(left_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.vm_listbox = tk.Listbox(list_frame, font=("Monospace", 10), activestyle="dotbox")
        self.vm_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vm_listbox.bind("<<ListboxSelect>>", self._on_vm_select)
        self.vm_listbox.bind("<Double-Button-1>", lambda e: self._on_start())

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.vm_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.vm_listbox.config(yscrollcommand=scrollbar.set)

        # VM action buttons
        action_frame = ttk.Frame(left_frame)
        action_frame.pack(fill=tk.X, pady=(5, 0))
        self.start_btn = ttk.Button(action_frame, text="▶ Start", command=self._on_start, state=tk.DISABLED)
        self.start_btn.pack(side=tk.LEFT, padx=2)
        self.stop_btn = ttk.Button(action_frame, text="■ Stop", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=2)
        self.pause_btn = ttk.Button(action_frame, text="⏸ Pause", command=self._on_pause_resume, state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, padx=2)
        self.reset_btn = ttk.Button(action_frame, text="⟳ Reset", command=self._on_reset, state=tk.DISABLED)
        self.reset_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(action_frame, text="📷 Screenshot", command=self._on_screenshot).pack(side=tk.LEFT, padx=2)
        ttk.Button(action_frame, text="⚙ Settings", command=self._on_settings).pack(side=tk.LEFT, padx=2)
        ttk.Button(action_frame, text="📸 Snapshots", command=self._on_snapshots).pack(side=tk.LEFT, padx=2)

        # ── Right pane: Details + Serial console ────────────────────
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)

        # Notebook for details / serial
        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Details tab
        details_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(details_frame, text="Details")

        self.detail_vars = {}
        detail_fields = [
            ("Name", "name"),
            ("Disk Path", "disk_path"),
            ("Memory", "memory_gb"),
            ("Resolution", "resolution"),
            ("Display", "display"),
            ("Audio", "audio"),
            ("Shared Folder", "shared_folder"),
            ("Kernel", "kernel"),
            ("Description", "description"),
            ("Status", "status"),
            ("Uptime", "uptime"),
        ]
        for i, (label, key) in enumerate(detail_fields):
            ttk.Label(details_frame, text=f"{label}:", font=("TkDefaultFont", 9, "bold")).grid(
                row=i, column=0, sticky=tk.NW, pady=3, padx=(0, 10))
            var = tk.StringVar(value="—")
            self.detail_vars[key] = var
            ttk.Label(details_frame, textvariable=var, wraplength=350).grid(
                row=i, column=1, sticky=tk.W, pady=3)

        # Serial console tab
        serial_frame = ttk.Frame(self.notebook)
        self.notebook.add(serial_frame, text="Serial Console")

        serial_toolbar = ttk.Frame(serial_frame)
        serial_toolbar.pack(fill=tk.X, pady=(5, 0), padx=5)
        ttk.Button(serial_toolbar, text="Clear", command=self._clear_serial).pack(side=tk.LEFT)
        ttk.Button(serial_toolbar, text="Follow", command=self._toggle_follow).pack(side=tk.LEFT, padx=5)
        self.follow_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(serial_toolbar, text="Auto-scroll", variable=self.follow_var).pack(side=tk.LEFT)

        self.serial_text = tk.Text(serial_frame, font=("Monospace", 9), wrap=tk.NONE, state=tk.DISABLED)
        self.serial_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        serial_scroll_y = ttk.Scrollbar(serial_frame, orient=tk.VERTICAL, command=self.serial_text.yview)
        serial_scroll_y.place(in_=self.serial_text, relx=1.0, rely=0, relheight=1.0, anchor=tk.NE)
        self.serial_text.config(yscrollcommand=serial_scroll_y.set)

        serial_scroll_x = ttk.Scrollbar(serial_frame, orient=tk.HORIZONTAL, command=self.serial_text.xview)
        serial_scroll_x.place(in_=self.serial_text, relx=0, rely=1.0, relwidth=1.0, anchor=tk.SW)
        self.serial_text.config(xscrollcommand=serial_scroll_x.set)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=5)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Screenshot display (initially hidden)
        self.screenshot_label = ttk.Label(self.root)
        self.screenshot_window = None

    def _start_auto_refresh(self):
        self._refresh_vm_list_periodic()
        self._refresh_serial_periodic()

    def _refresh_vm_list_periodic(self):
        self.refresh_vm_list()
        self._refresh_after_id = self.root.after(REFRESH_INTERVAL_MS, self._refresh_vm_list_periodic)

    def _refresh_serial_periodic(self):
        self._update_serial_console()
        self._serial_after_id = self.root.after(SERIAL_REFRESH_MS, self._refresh_serial_periodic)

    # ── VM List Operations ──────────────────────────────────────────

    def refresh_vm_list(self):
        """Reload VM list from store and update listbox."""
        try:
            names = self.store.list_names()
            self.vm_listbox.delete(0, tk.END)
            for name in names:
                try:
                    machine = Machine(name, self.store)
                    config = machine.config
                    state = machine.status()
                    self.vm_listbox.insert(tk.END, format_vm_row(config, state))
                except VmError:
                    self.vm_listbox.insert(tk.END, f"{name:<20} {'error':<8} — —")
            self.status_var.set(f"Refreshed — {len(names)} VM(s)")
        except Exception as e:
            self.status_var.set(f"Refresh failed: {e}")

    def _on_vm_select(self, event):
        sel = self.vm_listbox.curselection()
        if not sel:
            self.selected_vm_name = None
            self._clear_details()
            self._set_action_buttons(False)
            return
        # Extract VM name from the formatted row
        row_text = self.vm_listbox.get(sel[0])
        self.selected_vm_name = row_text.split()[0]
        self._update_details()
        self._set_action_buttons(True)
        self.serial_offset = 0  # reset serial tail on selection change

    def _set_action_buttons(self, enabled: bool):
        state = tk.NORMAL if enabled else tk.DISABLED
        for btn in (self.start_btn, self.stop_btn, self.pause_btn, self.reset_btn):
            btn.config(state=state)

    def _clear_details(self):
        for var in self.detail_vars.values():
            var.set("—")

    def _update_details(self):
        if not self.selected_vm_name:
            return
        try:
            machine = Machine(self.selected_vm_name, self.store)
            config = machine.config
            state = machine.status()

            self.detail_vars["name"].set(config.name)
            self.detail_vars["disk_path"].set(str(self.store.disk_path(config.name)))
            self.detail_vars["memory_gb"].set(f"{config.memory_gb} GB")
            self.detail_vars["resolution"].set(config.resolution)
            self.detail_vars["display"].set(config.display)
            self.detail_vars["audio"].set("On" if config.audio else "Off")
            self.detail_vars["shared_folder"].set(config.shared_folder or "(none)")
            self.detail_vars["kernel"].set(config.kernel or "(default)")
            self.detail_vars["description"].set(config.description or "(none)")
            self.detail_vars["status"].set(state.capitalize())

            # Uptime: only meaningful if running
            if state == "running":
                # We could parse boot time from serial log, but keep simple for now
                self.detail_vars["uptime"].set("Running (see serial console)")
            else:
                self.detail_vars["uptime"].set("Stopped")

        except VmError as e:
            self._clear_details()
            self.detail_vars["name"].set(self.selected_vm_name)
            self.detail_vars["status"].set(f"Error: {e}")

    # ── VM Actions (async) ──────────────────────────────────────────

    def _run_vm_action(self, action_name: str, func: Callable, success_msg: str, error_prefix: str):
        def on_done(_):
            self.status_var.set(success_msg)
            self.refresh_vm_list()
            self._update_details()

        def on_error(e):
            messagebox.showerror("Error", f"{error_prefix}: {e}", parent=self.root)
            self.status_var.set(f"Error: {e}")

        self.status_var.set(f"{action_name}...")
        self.runner.run(func, on_done, on_error)

    def _on_start(self):
        if not self.selected_vm_name:
            return
        machine = Machine(self.selected_vm_name, self.store)
        if machine.is_running():
            messagebox.showinfo("Already Running", f"VM '{self.selected_vm_name}' is already running.", parent=self.root)
            return
        self._run_vm_action("Starting", lambda: machine.start(), f"Started {self.selected_vm_name}", "Failed to start")

    def _on_stop(self):
        if not self.selected_vm_name:
            return
        machine = Machine(self.selected_vm_name, self.store)
        if not machine.is_running():
            messagebox.showinfo("Not Running", f"VM '{self.selected_vm_name}' is not running.", parent=self.root)
            return
        if messagebox.askyesno("Stop VM", f"Shut down '{self.selected_vm_name}' cleanly?", parent=self.root):
            self._run_vm_action("Stopping", lambda: machine.stop(), f"Stopped {self.selected_vm_name}", "Failed to stop")

    def _on_pause_resume(self):
        if not self.selected_vm_name:
            return
        machine = Machine(self.selected_vm_name, self.store)
        if not machine.is_running():
            return
        state = machine.status()
        if state == "paused":
            self._run_vm_action("Resuming", lambda: machine.resume(), f"Resumed {self.selected_vm_name}", "Failed to resume")
        else:
            self._run_vm_action("Pausing", lambda: machine.pause(), f"Paused {self.selected_vm_name}", "Failed to pause")

    def _on_reset(self):
        if not self.selected_vm_name:
            return
        machine = Machine(self.selected_vm_name, self.store)
        if not machine.is_running():
            return
        if messagebox.askyesno("Reset VM", f"Hard reset '{self.selected_vm_name}'?", parent=self.root):
            self._run_vm_action("Resetting", lambda: machine.reset(), f"Reset {self.selected_vm_name}", "Failed to reset")

    def _on_screenshot(self):
        if not self.selected_vm_name:
            return
        machine = Machine(self.selected_vm_name, self.store)
        if not machine.is_running():
            messagebox.showinfo("VM Stopped", "Cannot take screenshot: VM is not running.", parent=self.root)
            return

        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save Screenshot",
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png"), ("PPM Image", "*.ppm"), ("All files", "*.*")],
            initialfile=f"{self.selected_vm_name}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        )
        if not path:
            return

        def take_screenshot():
            return machine.screenshot(path)

        def on_done(result_path):
            self.status_var.set(f"Screenshot saved to {result_path}")
            self._show_screenshot_preview(result_path)

        def on_error(e):
            messagebox.showerror("Error", f"Screenshot failed: {e}", parent=self.root)

        self.runner.run(take_screenshot, on_done, on_error)

    def _show_screenshot_preview(self, path: str):
        """Show screenshot in a popup window."""
        if self.screenshot_window:
            self.screenshot_window.destroy()
        try:
            self.screenshot_window = tk.Toplevel(self.root)
            self.screenshot_window.title(f"Screenshot — {Path(path).name}")
            self.screenshot_window.transient(self.root)
            img = tk.PhotoImage(file=path)
            self.screenshot_label.config(image=img)
            self.screenshot_label.image = img  # keep reference
            self.screenshot_label.pack(padx=10, pady=10)
            ttk.Button(self.screenshot_window, text="Close", command=self.screenshot_window.destroy).pack(pady=(0, 10))
        except Exception as e:
            messagebox.showerror("Error", f"Could not display screenshot: {e}", parent=self.root)

    def _on_new_vm(self):
        dialog = NewVmDialog(self.root, self.store)
        if dialog.result:
            self.refresh_vm_list()
            # Select the new VM
            for i in range(self.vm_listbox.size()):
                if self.vm_listbox.get(i).startswith(dialog.result):
                    self.vm_listbox.selection_clear(0, tk.END)
                    self.vm_listbox.selection_set(i)
                    self.vm_listbox.see(i)
                    self._on_vm_select(None)
                    break

    def _on_settings(self):
        if not self.selected_vm_name:
            return
        machine = Machine(self.selected_vm_name, self.store)
        dialog = SettingsDialog(self.root, self.store, machine.config)
        if dialog.result:
            self.refresh_vm_list()
            self._update_details()

    def _on_snapshots(self):
        if not self.selected_vm_name:
            return
        machine = Machine(self.selected_vm_name, self.store)
        SnapshotDialog(self.root, machine, self.store, self.refresh_vm_list)

    # ── Serial Console ──────────────────────────────────────────────

    def _update_serial_console(self):
        if not self.selected_vm_name:
            return
        try:
            machine = Machine(self.selected_vm_name, self.store)
            data = machine.serial_bytes()
            if len(data) > self.serial_offset:
                new_text = data[self.serial_offset:].decode("utf-8", "replace")
                self.serial_offset = len(data)
                self.serial_text.config(state=tk.NORMAL)
                self.serial_text.insert(tk.END, new_text)
                if self.follow_var.get():
                    self.serial_text.see(tk.END)
                self.serial_text.config(state=tk.DISABLED)
        except Exception:
            pass  # Silently ignore serial read errors

    def _clear_serial(self):
        self.serial_text.config(state=tk.NORMAL)
        self.serial_text.delete(1.0, tk.END)
        self.serial_text.config(state=tk.DISABLED)
        if self.selected_vm_name:
            machine = Machine(self.selected_vm_name, self.store)
            self.serial_offset = len(machine.serial_bytes())

    def _toggle_follow(self):
        # Just a visual toggle; the actual follow behavior is in _update_serial_console
        pass

    def _show_about(self):
        messagebox.showinfo("About LobsterVM Manager",
                            "LobsterVM Manager\n\n"
                            "Graphical VM manager for LobsterOS Raspberry Pi 5 emulator.\n\n"
                            "Built with tkinter — no external dependencies.",
                            parent=self.root)


# ── Entry Points ────────────────────────────────────────────────────


def main(store: Optional[VmStore] = None) -> int:
    """Run the GUI manager. Returns exit code."""
    # Allow --help to work without opening a window
    if "--help" in sys.argv or "-h" in sys.argv:
        print("LobsterVM Graphical Manager")
        print("Usage: lobstervm gui [--help]")
        print("Opens the graphical VM manager window.")
        return 0

    root = tk.Tk()
    try:
        LobsterVmManager(root, store)
        root.mainloop()
        return 0
    except Exception as e:
        messagebox.showerror("Fatal Error", f"GUI failed to start: {e}")
        return 1


def launch() -> int:
    """Convenience entry point for CLI."""
    return main()


def run_manager(store: Optional[VmStore] = None) -> int:
    """Entry point expected by cli.py."""
    return main(store)


if __name__ == "__main__":
    sys.exit(main())