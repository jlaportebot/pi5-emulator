#!/usr/bin/env python3
"""
LobsterOS Auto-Demo: Automatically launches QEMU, waits for boot,
sends commands, and takes screenshots at each stage via grim.
Designed to run under Wayland/labwc where computer_use returns 0x0.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import queue
import time
import os
import json

QEMU_BIN = "/home/john/qemu-src/build/qemu-system-aarch64"
KERNEL_IMG = "/mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img"
DTB_FILE = "/home/john/pi5-emulator-repo/bcm2712-rpi-5-b.dtb"

QEMU_ARGS = [
    "-M", "raspi5b,graphics=on",
    "-m", "4G",
    "-cpu", "cortex-a76",
    "-smp", "4",
    "-kernel", KERNEL_IMG,
    "-dtb", DTB_FILE,
    "-serial", "stdio",
    "-display", "none",
    "-no-reboot",
]

BG_DARK   = "#1a1a2e"
BG_MED    = "#16213e"
BG_LIGHT  = "#0f3460"
FG_MAIN   = "#e0e0e0"
FG_ACCENT = "#e94560"
FG_GREEN  = "#4ecca3"
FG_YELLOW = "#ffd460"
FG_DIM    = "#888888"

SCREENSHOT_DIR = "/home/john/lobsteros_screenshots"


class LobsterOSAutoDemo:
    def __init__(self, root):
        self.root = root
        self.root.title("🦞 LobsterOS — Raspberry Pi 5 Emulator")
        self.root.geometry("900x680")
        self.root.minsize(700, 500)
        self.root.configure(bg=BG_DARK)

        self.proc = None
        self.running = False
        self.out_q = queue.Queue()
        self.history = []
        self.hist_idx = -1
        self.boot_complete = False
        self.demo_step = 0

        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

        self._build_ui()
        self._poll_output()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TFrame", background=BG_DARK)
        s.configure("TLabel", background=BG_DARK, foreground=FG_MAIN, font=("Segoe UI", 10))
        s.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground=FG_ACCENT)
        s.configure("Sub.TLabel", font=("Segoe UI", 9), foreground=FG_DIM)
        s.configure("St.TLabel", font=("Segoe UI", 10, "bold"))
        s.configure("TButton", font=("Segoe UI", 10), padding=6)
        s.map("TButton",
              background=[("active", FG_ACCENT), ("!active", BG_MED)],
              foreground=[("active", "#fff"), ("!active", FG_MAIN)])
        s.configure("Go.TButton", font=("Segoe UI", 12, "bold"))
        s.map("Go.TButton",
              background=[("active", "#c2364e"), ("!active", FG_ACCENT)],
              foreground=[("active", "#fff"), ("!active", "#fff")])
        s.configure("TEntry", fieldbackground=BG_LIGHT, foreground=FG_MAIN, bordercolor=BG_MED)

        hdr = ttk.Frame(main)
        hdr.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(hdr, text="🦞 LobsterOS", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(hdr, text="Raspberry Pi 5 Emulator • Bare-metal Rust OS • BCM2712 (4× Cortex-A76, 4 GB)",
                  style="Sub.TLabel").pack(anchor=tk.W)
        self.status = ttk.Label(hdr, text="● Ready", style="St.TLabel", foreground=FG_GREEN)
        self.status.pack(anchor=tk.W, pady=(4, 0))

        mono = ("JetBrains Mono", 10) if self._font_ok("JetBrains Mono") else ("Consolas", 10)
        self.mono = mono
        self.console = scrolledtext.ScrolledText(
            main, wrap=tk.WORD, font=mono,
            bg="#0d0d1a", fg="#c8c8d0",
            insertbackground="#fff", selectbackground=FG_ACCENT,
            borderwidth=0, highlightthickness=1,
            highlightbackground=FG_ACCENT, padx=10, pady=10,
        )
        self.console.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.console.config(state=tk.DISABLED)
        self._setup_tags(mono)

        self.progress = ttk.Progressbar(main, mode="indeterminate", length=200)

        inp = ttk.Frame(main)
        inp.pack(fill=tk.X, pady=(0, 8))
        self.prompt_lbl = ttk.Label(inp, text="lobster>", foreground=FG_ACCENT, font=mono)
        self.prompt_lbl.pack(side=tk.LEFT, padx=(0, 4))
        self.entry = tk.Entry(
            inp, font=mono,
            bg=BG_LIGHT, fg=FG_MAIN, insertbackground="#fff",
            relief=tk.FLAT, borderwidth=0, highlightthickness=1,
            highlightcolor=FG_ACCENT, highlightbackground=BG_MED,
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6), ipady=4)
        self.entry.bind("<Return>", self._send)
        clr_btn = ttk.Button(inp, text="Clear", command=self._clear, width=8)
        clr_btn.pack(side=tk.LEFT, padx=(4, 0))

        ctrl = ttk.Frame(main)
        ctrl.pack(fill=tk.X, pady=(0, 6))
        self.btn_start = ttk.Button(ctrl, text="🚀 Launch", command=self._start, style="Go.TButton")
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = ttk.Button(ctrl, text="⏹ Stop", command=self._stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_restart = ttk.Button(ctrl, text="↻ Restart", command=self._restart, state=tk.DISABLED)
        self.btn_restart.pack(side=tk.LEFT, padx=(0, 8))

        qc = ttk.Frame(main)
        qc.pack(fill=tk.X)
        ttk.Label(qc, text="Quick:", style="Sub.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        for label, cmd in [("help","help"),("ver","version"),("mem","mem"),("ps","ps"),
                          ("cpu","cpu"),("uptime","uptime"),("whoami","whoami"),("clear","clear")]:
            ttk.Button(qc, text=label, command=lambda c=cmd: self._quick(c), width=8).pack(side=tk.LEFT, padx=2)

    def _setup_tags(self, mono):
        tags = {
            "boot":    {"foreground": "#7a7a9a"},
            "banner":  {"foreground": "#e94560", "font": (mono[0], mono[1], "bold")},
            "success": {"foreground": FG_GREEN},
            "error":   {"foreground": "#ff5252"},
            "warn":    {"foreground": FG_YELLOW},
            "info":    {"foreground": "#54a0ff"},
            "shell":   {"foreground": "#ffffff"},
            "user":    {"foreground": FG_YELLOW, "font": (mono[0], mono[1], "bold")},
            "dim":     {"foreground": FG_DIM},
        }
        for tag, cfg in tags.items():
            self.console.tag_config(tag, **cfg)

    def _font_ok(self, name):
        try:
            import tkinter.font as f
            return name in f.families()
        except Exception:
            return False

    def _print(self, text, tag=None):
        self.console.config(state=tk.NORMAL)
        self.console.insert(tk.END, text, tag)
        self.console.see(tk.END)
        self.console.config(state=tk.DISABLED)

    def _clear(self, *_):
        self.console.config(state=tk.NORMAL)
        self.console.delete(1.0, tk.END)
        self.console.config(state=tk.DISABLED)

    def _colorize(self, line):
        if not line:
            return
        tag = None
        if any(k in line for k in ["[boot]","[dtb]","[gic]","[timer]","[mmu]","[frame-alloc]",
                                    "[heap]","[vmem]","[display]","[fs]","[users]","[setup]",
                                    "[sched]","[smp]","[runqueue]","[net]","[emmc]","[pkg]",
                                    "[usb]","[wm]","[apps]","[config]","[syslog]","[panic]",
                                    "[serial]","[hw]","[klog]"]):
            tag = "boot"
        elif any(k in line for k in ["LobsterOS","Bare-metal","Quad-core","Raspberry Pi 5",
                                      "╔","║","╚","====="]):
            tag = "banner"
        elif "lobster>" in line:
            tag = "shell"
            if not self.boot_complete:
                self.boot_complete = True
        elif any(k in line for k in ["PASSED","OK","enabled","initialized","complete","Working"]):
            tag = "success"
        elif any(k in line for k in ["FAILED","Error","error","timeout","failed","FAIL"]):
            tag = "error"
        elif any(k in line for k in ["warning","Warning","WARNING"]):
            tag = "warn"
        elif any(k in line for k in ["Memory Statistics","Total:","Used:","Free:"]):
            tag = "info"
        self._print(line, tag)

    def _check_files(self):
        for path, name in [(QEMU_BIN,"QEMU binary"),(KERNEL_IMG,"LobsterOS kernel"),(DTB_FILE,"DTB file")]:
            if not os.path.exists(path):
                return False
        return True

    def _start(self):
        if not self._check_files():
            return
        self._clear()
        self._print("╔═══════════════════════════════════════════════════════════════╗\n", "banner")
        self._print("║  🦞 LobsterOS — Raspberry Pi 5 Emulator                        ║\n", "banner")
        self._print("║  BCM2712 • 4× Cortex-A76 • 4 GB RAM • Custom QEMU v10.0.8    ║\n", "banner")
        self._print("╚═══════════════════════════════════════════════════════════════╝\n\n", "banner")

        self.running = True
        self.progress.pack(fill=tk.X, pady=(0, 6))
        self.progress.start(80)
        self.status.config(text="● Booting… (emulator takes ~15-20 s)", foreground=FG_YELLOW)
        self.btn_start.config(state=tk.DISABLED)

        self.entry.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_restart.config(state=tk.NORMAL)

        thread = threading.Thread(target=self._run_qemu, daemon=True)
        thread.start()

    def _run_qemu(self):
        try:
            self.proc = subprocess.Popen(
                [QEMU_BIN] + QEMU_ARGS,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            if self.proc.stdout:
                while self.running and self.proc.poll() is None:
                    line = self.proc.stdout.readline()
                    if line:
                        decoded = line.decode("utf-8", errors="replace")
                        self.out_q.put(decoded)
                    else:
                        break
            self.out_q.put("__DONE__")
        except Exception as e:
            self.out_q.put(f"[Error: {e}]\n")
            self.out_q.put("__DONE__")

    def _poll_output(self):
        try:
            while True:
                item = self.out_q.get_nowait()
                if item == "__DONE__":
                    self._on_ended()
                    break
                self._colorize(item)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_output)

    def _on_ended(self):
        self.running = False
        self.progress.stop()
        self.progress.pack_forget()
        self.status.config(text="● Stopped", foreground=FG_DIM)
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.btn_restart.config(state=tk.DISABLED)
        self._print("\n[Emulator stopped]\n", "dim")

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            self._print("\n[Stopping…]\n", "warn")
            self.running = False
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def _restart(self):
        self._stop()
        time.sleep(0.5)
        self._start()

    def _write_stdin(self, text):
        if self.proc and self.proc.poll() is None and self.proc.stdin:
            self.proc.stdin.write(text.encode())
            self.proc.stdin.flush()

    def _send(self, event=None):
        cmd = self.entry.get().strip()
        if not cmd:
            return
        self._send_cmd(cmd)
        self.entry.delete(0, tk.END)

    def _send_cmd(self, cmd):
        self._print(f"lobster> {cmd}\n", "user")
        self._write_stdin(cmd + "\n")
        self.history.append(cmd)
        self.hist_idx = len(self.history)

    def _quick(self, cmd):
        self._send_cmd(cmd)

    def _hist_up(self, _):
        if self.history and self.hist_idx > 0:
            self.hist_idx -= 1
            self.entry.delete(0, tk.END)
            self.entry.insert(0, self.history[self.hist_idx])
        return "break"

    def _hist_down(self, _):
        if self.hist_idx < len(self.history) - 1:
            self.hist_idx += 1
            self.entry.delete(0, tk.END)
            self.entry.insert(0, self.history[self.hist_idx])
        elif self.hist_idx >= len(self.history) - 1:
            self.hist_idx = len(self.history)
            self.entry.delete(0, tk.END)
        return "break"

    def _on_close(self):
        self._stop()
        self.root.destroy()

    # ── Automated demo ──
    def _screenshot(self, name):
        path = os.path.join(SCREENSHOT_DIR, name)
        env = os.environ.copy()
        env["WAYLAND_DISPLAY"] = "wayland-0"
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        subprocess.run(["grim", path], env=env, capture_output=True)
        if os.path.exists(path):
            self._print(f"\n[📸 Screenshot saved: {name}]\n", "info")

    def _run_automation(self):
        """Background thread that drives the demo: launch → wait → send commands → screenshot."""
        # Step 1: Auto-launch
        time.sleep(1)
        self.root.after(0, self._start)
        time.sleep(2)

        # Step 2: Screenshot the launching state
        self.root.after(0, lambda: self._screenshot("01_launching.png"))

        # Step 3: Wait for boot (up to 30 seconds)
        for i in range(60):
            if self.boot_complete:
                break
            time.sleep(0.5)

        time.sleep(2)

        # Step 4: Screenshot the booted state
        self.root.after(0, lambda: self._screenshot("02_booted.png"))

        # Step 5: Send commands one at a time, with screenshots
        commands = [
            ("help", "03_help.png"),
            ("version", "04_version.png"),
            ("mem", "05_mem.png"),
            ("ps", "06_ps.png"),
            ("whoami", "07_whoami.png"),
            ("uptime", "08_uptime.png"),
        ]

        for cmd, screenshot_name in commands:
            time.sleep(1)
            self._send_cmd(cmd)
            time.sleep(1.5)
            self.root.after(0, lambda sn=screenshot_name: self._screenshot(sn))

        # Step 6: Final screenshot
        time.sleep(1)
        self.root.after(0, lambda: self._screenshot("09_final.png"))

        # Step 7: Stop
        time.sleep(2)
        self.root.after(0, self._stop)


def main():
    root = tk.Tk()
    app = LobsterOSAutoDemo(root)

    # Start automation in background thread
    auto_thread = threading.Thread(target=app._run_automation, daemon=True)
    auto_thread.start()

    root.mainloop()


if __name__ == "__main__":
    main()
