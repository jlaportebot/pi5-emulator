#!/usr/bin/env python3
"""
LobsterOS Desktop Launcher — Single unified launcher
Opens a Tk control panel + QEMU GTK graphical framebuffer window.
Click "Launch" to boot LobsterOS with full GUI desktop.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import queue
import time
import os
import sys

# ─── Configuration ─────────────────────────────────────────────────────
QEMU_BIN = "/home/john/qemu-src/build/qemu-system-aarch64"
KERNEL_IMG = "/mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img"
DTB_FILE = "/home/john/pi5-emulator-repo/bcm2712-rpi-5-b.dtb"


class LobsterOSLauncher:
    """Tk-based control panel that launches QEMU with graphical display."""

    def __init__(self, root):
        self.root = root
        self.root.title("🦞 LobsterOS — Raspberry Pi 5 Emulator")
        self.root.geometry("1000x700")
        self.root.minsize(700, 500)

        # Process management
        self.process = None
        self.running = False
        self.output_queue = queue.Queue()
        self.boot_complete = False
        self.boot_start_time = 0

        # Command history
        self.command_history = []
        self.history_index = -1

        # Setup UI
        self._setup_styles()
        self._create_widgets()
        self._setup_layout()
        self._setup_console_tags()

        # Start output processing
        self._poll_output()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─── Styles ─────────────────────────────────────────────────────────
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        BG_DARK = "#1a1a2e"
        BG_MED = "#16213e"
        BG_LIGHT = "#0f3460"
        FG_MAIN = "#e0e0e0"
        FG_ACCENT = "#e94560"
        FG_GREEN = "#4ecca3"
        FG_YELLOW = "#ffd460"
        FG_DIM = "#888888"

        self.root.configure(bg=BG_DARK)

        style.configure("TFrame", background=BG_DARK)
        style.configure("TLabel", background=BG_DARK, foreground=FG_MAIN,
                         font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"),
                         foreground=FG_ACCENT)
        style.configure("Subtitle.TLabel", font=("Segoe UI", 9),
                         foreground=FG_DIM)
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.map("TButton",
                  background=[("active", FG_ACCENT), ("!active", BG_MED)],
                  foreground=[("active", "#fff"), ("!active", FG_MAIN)])
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"))
        style.map("Accent.TButton",
                  background=[("active", "#c2364e"), ("!active", FG_ACCENT)],
                  foreground=[("active", "#fff"), ("!active", "#fff")])
        style.configure("TEntry", fieldbackground=BG_LIGHT,
                         foreground=FG_MAIN, bordercolor=BG_LIGHT)
        style.configure("Horizontal.TProgressbar", background=FG_ACCENT,
                         troughcolor=BG_MED)

        self.colors = {
            "bg": BG_DARK, "fg": FG_MAIN, "accent": FG_ACCENT,
            "green": FG_GREEN, "yellow": FG_YELLOW, "blue": "#54a0ff",
            "dim": FG_DIM, "error": "#ff5252", "boot": "#7a7a9a",
            "kernel": "#e94560", "shell": "#ffffff", "user_input": FG_YELLOW,
        }

    def _font_exists(self, name):
        try:
            import tkinter.font as f
            return name in f.families()
        except Exception:
            return False

    def _get_mono_font(self, bold=False):
        for name in ["JetBrains Mono", "DejaVu Sans Mono",
                     "Liberation Mono", "Courier"]:
            if self._font_exists(name):
                style = "bold" if bold else "normal"
                return (name, 10, style)
        return ("Courier", 10, "bold" if bold else "normal")

    # ─── Widgets ────────────────────────────────────────────────────────
    def _create_widgets(self):
        self.main_frame = ttk.Frame(self.root, padding=12)

        # Header
        self.header_frame = ttk.Frame(self.main_frame)
        self.title_label = ttk.Label(self.header_frame, text="🦞 LobsterOS",
                                     style="Title.TLabel")
        self.subtitle_label = ttk.Label(
            self.header_frame,
            text="Raspberry Pi 5 Emulator • Bare-metal Rust OS • BCM2712 (4× Cortex-A76, 4 GB)",
            style="Subtitle.TLabel"
        )
        self.status_label = ttk.Label(self.header_frame, text="● Ready to launch",
                                      style="Status.TLabel",
                                      foreground=self.colors["green"])
        self.info_label = ttk.Label(
            self.header_frame,
            text="Launch opens a QEMU GTK window showing the graphical desktop. Type commands in the serial console below.",
            style="Subtitle.TLabel"
        )

        # Console area
        self.console_frame = ttk.Frame(self.main_frame)
        self.console = scrolledtext.ScrolledText(
            self.console_frame, wrap=tk.WORD,
            font=self._get_mono_font(),
            bg="#0d0d1a", fg="#c8c8d0",
            insertbackground="#fff",
            selectbackground=self.colors["accent"],
            selectforeground="#ffffff",
            borderwidth=0, highlightthickness=1,
            highlightbackground=self.colors["accent"],
            padx=10, pady=10
        )
        self.console.config(state=tk.DISABLED)

        # Input area
        self.input_frame = ttk.Frame(self.main_frame)
        self.input_label = ttk.Label(self.input_frame, text="lobster>",
                                     foreground=self.colors["accent"],
                                     font=self._get_mono_font())
        self.input_entry = ttk.Entry(self.input_frame,
                                     font=self._get_mono_font())
        self.input_entry.bind("<Return>", self._on_send)
        self.input_entry.bind("<Up>", self._history_up)
        self.input_entry.bind("<Down>", self._history_down)
        self.send_button = ttk.Button(self.input_frame, text="Send",
                                      command=self._on_send,
                                      style="Accent.TButton", width=8)
        self.clear_button = ttk.Button(self.input_frame, text="Clear",
                                       command=self._clear_console, width=8)

        # Control buttons
        self.control_frame = ttk.Frame(self.main_frame)
        self.launch_button = ttk.Button(
            self.control_frame, text="🚀 Launch LobsterOS",
            command=self._launch, style="Accent.TButton")
        self.stop_button = ttk.Button(
            self.control_frame, text="⏹ Stop",
            command=self._stop, state=tk.DISABLED)
        self.restart_button = ttk.Button(
            self.control_frame, text="🔄 Restart",
            command=self._restart, state=tk.DISABLED)
        self.progress = ttk.Progressbar(self.control_frame,
                                        mode="indeterminate", length=200)

        # Quick command buttons
        self.quick_frame = ttk.Frame(self.main_frame)
        self.quick_label = ttk.Label(self.quick_frame, text="Quick commands:",
                                    style="Subtitle.TLabel")
        quick_commands = [
            ("help", "help"), ("version", "version"),
            ("mem", "mem"), ("ps", "ps"),
            ("cpu", "cpu"), ("uptime", "uptime"),
            ("whoami", "whoami"), ("clear", "clear"),
        ]
        self.quick_buttons = []
        for label, cmd in quick_commands:
            btn = ttk.Button(self.quick_frame, text=label,
                             command=lambda c=cmd: self._quick(c),
                             width=10)
            self.quick_buttons.append(btn)

    def _setup_layout(self):
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        self.header_frame.pack(fill=tk.X, pady=(0, 10))
        self.title_label.pack(anchor=tk.W)
        self.subtitle_label.pack(anchor=tk.W)
        self.info_label.pack(anchor=tk.W, pady=(2, 0))
        self.status_label.pack(anchor=tk.W, pady=(5, 0))

        # Console
        self.console_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self.console.pack(fill=tk.BOTH, expand=True)

        # Input
        self.input_frame.pack(fill=tk.X, pady=(0, 10))
        self.input_label.pack(side=tk.LEFT, padx=(0, 5))
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                              padx=(0, 5))
        self.send_button.pack(side=tk.LEFT, padx=(0, 5))
        self.clear_button.pack(side=tk.LEFT)

        # Controls
        self.control_frame.pack(fill=tk.X, pady=(0, 10))
        self.launch_button.pack(side=tk.LEFT, padx=(0, 10))
        self.stop_button.pack(side=tk.LEFT, padx=(0, 10))
        self.restart_button.pack(side=tk.LEFT, padx=(0, 10))
        self.progress.pack(side=tk.LEFT, padx=(20, 0))

        # Quick commands
        self.quick_frame.pack(fill=tk.X)
        self.quick_label.pack(anchor=tk.W, pady=(0, 5))
        quick_row = ttk.Frame(self.quick_frame)
        quick_row.pack(fill=tk.X)
        for btn in self.quick_buttons:
            btn.pack(side=tk.LEFT, padx=(0, 5))

    # ─── Console tags ───────────────────────────────────────────────────
    def _setup_console_tags(self):
        mono = self._get_mono_font()
        tags = {
            "boot": {"foreground": self.colors["boot"]},
            "kernel": {"foreground": self.colors["kernel"]},
            "shell": {"foreground": self.colors["shell"],
                      "font": self._get_mono_font(bold=True)},
            "user_input": {"foreground": self.colors["user_input"]},
            "success": {"foreground": self.colors["green"]},
            "error": {"foreground": self.colors["error"]},
            "warning": {"foreground": self.colors["yellow"]},
            "info": {"foreground": self.colors["blue"]},
            "dim": {"foreground": self.colors["dim"]},
        }
        for tag, cfg in tags.items():
            self.console.tag_config(tag, **cfg)

    # ─── Console helpers ────────────────────────────────────────────────
    def _append(self, text, tag=None):
        self.console.config(state=tk.NORMAL)
        if tag:
            self.console.insert(tk.END, text, tag)
        else:
            self.console.insert(tk.END, text)
        self.console.see(tk.END)
        self.console.config(state=tk.DISABLED)

    def _clear_console(self):
        self.console.config(state=tk.NORMAL)
        self.console.delete(1.0, tk.END)
        self.console.config(state=tk.DISABLED)

    def _colorize(self, line):
        boot_keywords = [
            "[boot]", "[dtb]", "[gic]", "[timer]", "[mmu]", "[frame-alloc]",
            "[heap]", "[vmem]", "[display]", "[fb]", "[fs]", "[users]",
            "[setup]", "[sched]", "[smp]", "[runqueue]", "[net]", "[emmc]",
            "[pkg]", "[usb]", "[wm]", "[apps]", "[terminal]", "[file_manager]",
            "[editor]", "[settings]", "[config]", "[syslog]", "[panic]",
            "[serial]", "[hw]", "[compositor]", "[panel]", "[power]",
            "[audio]", "[gpio]", "[bt]", "[shell]", "[wm::input]",
            "[theme]", "[klog]",
        ]
        tag = None
        if any(kw in line for kw in boot_keywords):
            tag = "boot"
        elif any(kw in line for kw in
                 ["LobsterOS", "Bare-metal", "Quad-core", "Raspberry Pi 5",
                  "╔", "║", "╚", "═══"]):
            tag = "kernel"
        elif "lobster>" in line:
            tag = "shell"
        elif any(kw in line for kw in
                 ["PASSED", "OK", "enabled", "initialized", "complete",
                  "Working", "created"]):
            tag = "success"
        elif any(kw in line for kw in
                 ["FAILED", "Error", "error", "timeout", "failed", "FAIL"]):
            tag = "error"
        elif any(kw in line for kw in ["warning", "Warning", "WARNING"]):
            tag = "warning"
        elif any(kw in line for kw in
                 ["Memory Statistics", "Total:", "Used:", "Free:"]):
            tag = "info"
        self._append(line, tag)

    # ─── Output polling ────────────────────────────────────────────────
    def _poll_output(self):
        try:
            while True:
                item = self.output_queue.get_nowait()
                if item == "__PROCESS_ENDED__":
                    self._on_ended()
                    break
                elif item == "__BOOT_COMPLETE__":
                    self._on_boot_complete()
                    break
                else:
                    self._colorize(item)
        except queue.Empty:
            pass
        finally:
            self.root.after(50, self._poll_output)

    def _on_boot_complete(self):
        self.boot_complete = True
        self.progress.stop()
        self.progress.pack_forget()
        self.status_label.config(text="● Running — Shell ready",
                                 foreground=self.colors["green"])
        self.input_entry.config(state=tk.NORMAL)
        self.send_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL)
        self.restart_button.config(state=tk.NORMAL)
        self.launch_button.config(state=tk.DISABLED)
        for btn in self.quick_buttons:
            btn.config(state=tk.NORMAL)
        self.input_entry.focus()

    def _on_ended(self):
        self.running = False
        self.boot_complete = False
        self.progress.stop()
        self.progress.pack_forget()
        self.status_label.config(text="● Stopped",
                                 foreground=self.colors["dim"])
        self.input_entry.config(state=tk.DISABLED)
        self.send_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.restart_button.config(state=tk.DISABLED)
        self.launch_button.config(state=tk.NORMAL)
        for btn in self.quick_buttons:
            btn.config(state=tk.DISABLED)
        self._append("\n[Emulator stopped]\n", "dim")

    # ─── Launch ─────────────────────────────────────────────────────────
    def _check_files(self):
        for path, name in [(QEMU_BIN, "QEMU binary"),
                           (KERNEL_IMG, "Kernel image"),
                           (DTB_FILE, "DTB file")]:
            if not os.path.exists(path):
                messagebox.showerror("Missing File",
                                     f"{name} not found:\n{path}")
                return False
        return True

    def _launch(self):
        if not self._check_files():
            return

        self._clear_console()
        self._append(
            "╔═══════════════════════════════════════════════════════════════╗\n",
            "kernel")
        self._append(
            "║  🦞 LobsterOS — Raspberry Pi 5 Emulator                        ║\n",
            "kernel")
        self._append(
            "║  BCM2712 • 4× Cortex-A76 • 4 GB RAM • Custom QEMU             ║\n",
            "kernel")
        self._append(
            "╚═══════════════════════════════════════════════════════════════╝\n\n",
            "kernel")
        self._append("Opening QEMU GTK window (graphical desktop)...\n",
                     "info")
        self._append("Type commands in the input box below — they go to the serial console.\n\n",
                     "info")

        self.running = True
        self.boot_complete = False
        self.boot_start_time = time.time()
        self.progress.pack(fill=tk.X, pady=(0, 6))
        self.progress.start(100)
        self.status_label.config(text="● Booting LobsterOS...",
                                 foreground=self.colors["yellow"])
        self.launch_button.config(state=tk.DISABLED)
        self.input_entry.config(state=tk.DISABLED)
        self.send_button.config(state=tk.DISABLED)
        for btn in self.quick_buttons:
            btn.config(state=tk.DISABLED)

        thread = threading.Thread(target=self._run_qemu, daemon=True)
        thread.start()

    def _run_qemu(self):
        """Run QEMU with GTK graphical display in a background thread."""
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")

        qemu_args = [
            QEMU_BIN,
            "-M", "raspi5b,graphics=on",
            "-m", "4G",
            "-cpu", "cortex-a76",
            "-kernel", KERNEL_IMG,
            "-dtb", DTB_FILE,
            "-serial", "stdio",
            "-display", "gtk,show-tabs=off,show-menubar=off,show-cursor=on,grab-on-hover=on",
            "-device", "usb-mouse",
            "-device", "usb-kbd",
            "-no-reboot",
            "-no-shutdown",
        ]

        try:
            self.process = subprocess.Popen(
                qemu_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )

            if self.process.stdout:
                while self.running and self.process.poll() is None:
                    try:
                        line = self.process.stdout.readline()
                        if line:
                            self.output_queue.put(line)
                            # Detect boot completion
                            if not self.boot_complete and "lobster>" in line:
                                time.sleep(0.3)
                                self.output_queue.put("__BOOT_COMPLETE__")
                            # Fallback: if boot takes >25 seconds, assume it's ready
                            elif not self.boot_complete and \
                                    (time.time() - self.boot_start_time) > 25:
                                time.sleep(0.3)
                                self.output_queue.put("__BOOT_COMPLETE__")
                        else:
                            break
                    except Exception as e:
                        self.output_queue.put(f"[Read error: {e}]\n")
                        break
        except Exception as e:
            self.output_queue.put(f"[Error starting QEMU: {e}]\n")
        finally:
            self.output_queue.put("__PROCESS_ENDED__\n")

    # ─── Stop / Restart ────────────────────────────────────────────────
    def _stop(self):
        if self.process and self.process.poll() is None:
            self._append("\n[Stopping emulator...]\n", "warning")
            self.running = False
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def _restart(self):
        self._stop()
        time.sleep(0.5)
        self._launch()

    # ─── Command input ──────────────────────────────────────────────────
    def _on_send(self, event=None):
        cmd = self.input_entry.get().strip()
        if cmd and self.running and self.process and \
                self.process.poll() is None:
            self._append(f"lobster> {cmd}\n", "user_input")
            try:
                if self.process.stdin:
                    self.process.stdin.write(cmd + "\n")
                    self.process.stdin.flush()
                    self.command_history.append(cmd)
                    self.history_index = len(self.command_history)
            except Exception as e:
                self._append(f"[Send error: {e}]\n", "error")
        self.input_entry.delete(0, tk.END)

    def _quick(self, cmd):
        if self.running and self.process and self.process.poll() is None:
            self._append(f"lobster> {cmd}\n", "user_input")
            try:
                if self.process.stdin:
                    self.process.stdin.write(cmd + "\n")
                    self.process.stdin.flush()
                    self.command_history.append(cmd)
                    self.history_index = len(self.command_history)
            except Exception as e:
                self._append(f"[Send error: {e}]\n", "error")

    def _history_up(self, event):
        if self.command_history and self.history_index > 0:
            self.history_index -= 1
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, self.command_history[self.history_index])
        return "break"

    def _history_down(self, event):
        if self.command_history and self.history_index < len(self.command_history) - 1:
            self.history_index += 1
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, self.command_history[self.history_index])
        elif self.history_index >= len(self.command_history) - 1:
            self.history_index = len(self.command_history)
            self.input_entry.delete(0, tk.END)
        return "break"

    # ─── Close ──────────────────────────────────────────────────────────
    def _on_close(self):
        if self.running:
            if messagebox.askyesno("Confirm Exit",
                                    "Emulator is running. Stop and exit?"):
                self._stop()
                self.root.after(500, self.root.destroy)
        else:
            self.root.destroy()


def main():
    # Ensure DISPLAY is set for Tk + QEMU GTK
    os.environ.setdefault("DISPLAY", ":0")
    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
    os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")

    root = tk.Tk()
    app = LobsterOSLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
