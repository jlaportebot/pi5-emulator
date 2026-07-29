#!/usr/bin/env python3
"""
LobsterOS Desktop Launcher - Final Working Version
A polished GUI application to run the Pi 5 emulator with LobsterOS.
Click the desktop icon to launch!
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import queue
import time
import os
import sys

# Configuration - paths to emulator components
QEMU_BIN = "/home/john/qemu-src/build/qemu-system-aarch64"
KERNEL_IMG = "/mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img"
DTB_FILE = "/home/john/pi5-emulator-repo/bcm2712-rpi-5-b.dtb"

QEMU_ARGS = [
    "-M", "raspi5b",
    "-m", "4G",
    "-cpu", "cortex-a76",
    "-smp", "4",
    "-kernel", KERNEL_IMG,
    "-dtb", DTB_FILE,
    "-serial", "stdio",
    "-display", "none",
    "-no-reboot"
]


class LobsterOSLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("LobsterOS - Raspberry Pi 5 Emulator")
        self.root.geometry("900x650")
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
        self.setup_styles()
        self.create_widgets()
        self.setup_layout()
        
        # Start output processing
        self.process_output()
        
        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
    def setup_styles(self):
        """Configure ttk styles for a modern look"""
        style = ttk.Style()
        style.theme_use('clam')
        
        # Colors - LobsterOS theme (dark with coral accents)
        bg_dark = "#1e1e2e"
        bg_medium = "#282838"
        bg_light = "#353545"
        fg_main = "#e0e0e0"
        fg_accent = "#ff6b6b"  # Coral/lobster red
        fg_green = "#4ec9b0"
        fg_yellow = "#ffcc00"
        fg_blue = "#5dade2"
        fg_dim = "#888888"
        
        self.root.configure(bg=bg_dark)
        
        style.configure("TFrame", background=bg_dark)
        style.configure("TLabel", background=bg_dark, foreground=fg_main, font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"), foreground=fg_accent)
        style.configure("Subtitle.TLabel", font=("Segoe UI", 9), foreground=fg_dim)
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=8)
        style.map("TButton",
            background=[("active", fg_accent), ("!active", bg_medium)],
            foreground=[("active", "#ffffff"), ("!active", fg_main)])
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"))
        style.map("Accent.TButton",
            background=[("active", "#ff5252"), ("!active", fg_accent)],
            foreground=[("active", "#ffffff"), ("!active", "#ffffff")])
        style.configure("TEntry", fieldbackground=bg_light, foreground=fg_main, bordercolor=bg_light)
        style.configure("Horizontal.TProgressbar", background=fg_accent, troughcolor=bg_medium)
        
        # Store colors for console
        self.colors = {
            "bg": bg_dark,
            "fg": fg_main,
            "accent": fg_accent,
            "green": fg_green,
            "yellow": fg_yellow,
            "blue": fg_blue,
            "dim": fg_dim,
            "error": "#ff5252",
            "boot": "#888888",
            "kernel": "#aaaaaa",
            "shell": "#ffffff",
            "user_input": fg_yellow,
        }
        
    def create_widgets(self):
        """Create all UI widgets"""
        # Main container
        self.main_frame = ttk.Frame(self.root, padding=10)
        
        # Header
        self.header_frame = ttk.Frame(self.main_frame)
        self.title_label = ttk.Label(self.header_frame, text="🦞 LobsterOS", style="Title.TLabel")
        self.subtitle_label = ttk.Label(self.header_frame, text="Raspberry Pi 5 Emulator • Bare-metal Rust OS", style="Subtitle.TLabel")
        self.status_label = ttk.Label(self.header_frame, text="● Ready to launch", style="Status.TLabel", foreground=self.colors["green"])
        
        # Console area
        self.console_frame = ttk.Frame(self.main_frame)
        self.console = scrolledtext.ScrolledText(
            self.console_frame,
            wrap=tk.WORD,
            font=("JetBrains Mono", 10) if self.font_exists("JetBrains Mono") else ("Consolas", 10),
            bg=self.colors["bg"],
            fg=self.colors["fg"],
            insertbackground=self.colors["fg"],
            selectbackground=self.colors["accent"],
            selectforeground="#ffffff",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.colors["accent"],
            padx=10,
            pady=10
        )
        self.console.config(state=tk.DISABLED)
        
        # Configure console tags for colored output
        self.setup_console_tags()
        
        # Input area
        self.input_frame = ttk.Frame(self.main_frame)
        self.input_label = ttk.Label(self.input_frame, text="lobster>", foreground=self.colors["accent"], 
                                     font=("JetBrains Mono", 10) if self.font_exists("JetBrains Mono") else ("Consolas", 10))
        self.input_entry = ttk.Entry(self.input_frame, 
                                     font=("JetBrains Mono", 10) if self.font_exists("JetBrains Mono") else ("Consolas", 10))
        self.input_entry.bind("<Return>", self.on_send_command)
        self.input_entry.bind("<Up>", self.on_history_up)
        self.input_entry.bind("<Down>", self.on_history_down)
        self.send_button = ttk.Button(self.input_frame, text="Send", command=self.on_send_command, style="Accent.TButton", width=8)
        self.clear_button = ttk.Button(self.input_frame, text="Clear", command=self.clear_console, width=8)
        
        # Control buttons
        self.control_frame = ttk.Frame(self.main_frame)
        self.launch_button = ttk.Button(self.control_frame, text="🚀 Launch LobsterOS", command=self.launch_emulator, style="Accent.TButton")
        self.stop_button = ttk.Button(self.control_frame, text="⏹ Stop", command=self.stop_emulator, state=tk.DISABLED)
        self.restart_button = ttk.Button(self.control_frame, text="🔄 Restart", command=self.restart_emulator, state=tk.DISABLED)
        
        # Progress bar (indeterminate during boot)
        self.progress = ttk.Progressbar(self.control_frame, mode="indeterminate", length=200)
        
        # Quick command buttons
        self.quick_frame = ttk.Frame(self.main_frame)
        self.quick_label = ttk.Label(self.quick_frame, text="Quick commands:", style="Subtitle.TLabel")
        quick_commands = [
            ("help", "help"),
            ("version", "version"),
            ("mem", "mem"),
            ("ps", "ps"),
            ("cpu", "cpu"),
            ("uptime", "uptime"),
            ("whoami", "whoami"),
            ("clear", "clear"),
        ]
        self.quick_buttons = []
        for label, cmd in quick_commands:
            btn = ttk.Button(self.quick_frame, text=label, command=lambda c=cmd: self.send_quick_command(c), width=10)
            self.quick_buttons.append((btn, cmd))
        
    def font_exists(self, font_name):
        """Check if a font is available"""
        try:
            import tkinter.font as tkfont
            return font_name in tkfont.families()
        except:
            return False
        
    def setup_console_tags(self):
        """Configure text tags for colored console output"""
        tags = {
            "boot": {"foreground": self.colors["boot"]},
            "kernel": {"foreground": self.colors["kernel"]},
            "shell": {"foreground": self.colors["shell"], "font": ("JetBrains Mono", 10, "bold") if self.font_exists("JetBrains Mono") else ("Consolas", 10, "bold")},
            "user_input": {"foreground": self.colors["user_input"]},
            "success": {"foreground": self.colors["green"]},
            "error": {"foreground": self.colors["error"]},
            "warning": {"foreground": self.colors["yellow"]},
            "info": {"foreground": self.colors["blue"]},
            "dim": {"foreground": self.colors["dim"]},
        }
        for tag, config in tags.items():
            self.console.tag_config(tag, **config)
            
    def setup_layout(self):
        """Arrange widgets in the window"""
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        self.header_frame.pack(fill=tk.X, pady=(0, 10))
        self.title_label.pack(anchor=tk.W)
        self.subtitle_label.pack(anchor=tk.W)
        
        # Status row
        status_row = ttk.Frame(self.header_frame)
        status_row.pack(fill=tk.X, pady=(5, 0))
        self.status_label.pack(side=tk.LEFT)
        
        # Console
        self.console_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self.console.pack(fill=tk.BOTH, expand=True)
        
        # Input area
        self.input_frame.pack(fill=tk.X, pady=(0, 10))
        self.input_label.pack(side=tk.LEFT, padx=(0, 5))
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.send_button.pack(side=tk.LEFT, padx=(0, 5))
        self.clear_button.pack(side=tk.LEFT)
        
        # Control buttons
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
        for i, (btn, cmd) in enumerate(self.quick_buttons):
            btn.pack(side=tk.LEFT, padx=(0, 5))
            if i == len(self.quick_buttons) - 1:
                btn.pack(side=tk.LEFT)  # No extra padding on last
        
    def append_console(self, text, tag=None):
        """Append text to console with optional tag"""
        self.console.config(state=tk.NORMAL)
        if tag:
            self.console.insert(tk.END, text, tag)
        else:
            self.console.insert(tk.END, text)
        self.console.see(tk.END)
        self.console.config(state=tk.DISABLED)
        
    def clear_console(self):
        """Clear the console output"""
        self.console.config(state=tk.NORMAL)
        self.console.delete(1.0, tk.END)
        self.console.config(state=tk.DISABLED)
        
    def process_output(self):
        """Process output queue from emulator thread"""
        try:
            while True:
                line = self.output_queue.get_nowait()
                if line == "__PROCESS_ENDED__":
                    self.on_process_ended()
                    break
                elif line == "__BOOT_COMPLETE__":
                    self.on_boot_complete()
                    break
                else:
                    self.colorize_and_append(line)
        except queue.Empty:
            pass
        finally:
            self.root.after(50, self.process_output)
            
    def colorize_and_append(self, line):
        """Add color tags based on line content"""
        tag = None
        if any(kw in line for kw in ["[boot]", "[dtb]", "[gic]", "[timer]", "[mmu]", "[frame-alloc]", "[heap]", "[vmem]", "[display]", "[fs]", "[users]", "[setup]", "[sched]", "[smp]", "[runqueue]", "[net]", "[emmc]", "[pkg]", "[usb]", "[wm]", "[apps]", "[terminal]", "[file_manager]", "[editor]", "[settings]", "[config]", "[syslog]", "[panic]", "[serial]", "[hw]"]):
            tag = "boot"
        elif any(kw in line for kw in ["LobsterOS", "Bare-metal", "Quad-core", "Raspberry Pi 5", "╔", "║", "╚"]):
            tag = "kernel"
        elif "lobster>" in line:
            tag = "shell"
        elif any(kw in line for kw in ["PASSED", "OK", "enabled", "initialized", "complete", "Working"]):
            tag = "success"
        elif any(kw in line for kw in ["FAILED", "Error", "error", "timeout", "failed", "FAIL"]):
            tag = "error"
        elif any(kw in line for kw in ["warning", "Warning", "WARNING"]):
            tag = "warning"
        elif any(kw in line for kw in ["Memory Statistics", "Total:", "Used:", "Free:"]):
            tag = "info"
            
        self.append_console(line, tag)
        
    def on_boot_complete(self):
        """Called when boot sequence completes"""
        self.boot_complete = True
        self.progress.stop()
        self.status_label.config(text="● Running - Shell ready", foreground=self.colors["green"])
        self.input_entry.config(state=tk.NORMAL)
        self.send_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL)
        self.restart_button.config(state=tk.NORMAL)
        self.launch_button.config(state=tk.DISABLED)
        for btn, _ in self.quick_buttons:
            btn.config(state=tk.NORMAL)
        self.input_entry.focus()
        
    def on_process_ended(self):
        """Called when QEMU process ends"""
        self.running = False
        self.boot_complete = False
        self.progress.stop()
        self.status_label.config(text="● Stopped", foreground=self.colors["dim"])
        self.input_entry.config(state=tk.DISABLED)
        self.send_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.restart_button.config(state=tk.DISABLED)
        self.launch_button.config(state=tk.NORMAL)
        for btn, _ in self.quick_buttons:
            btn.config(state=tk.DISABLED)
        self.append_console("\n[Emulator stopped]\n", "dim")
        
    def launch_emulator(self):
        """Start the QEMU emulator"""
        # Validate files exist
        for path, name in [(QEMU_BIN, "QEMU binary"), (KERNEL_IMG, "Kernel image"), (DTB_FILE, "DTB file")]:
            if not os.path.exists(path):
                messagebox.showerror("Missing File", f"{name} not found:\n{path}")
                return
                
        self.clear_console()
        self.append_console("╔══════════════════════════════════════════════════════════════╗\n", "kernel")
        self.append_console("║  🦞 LobsterOS - Raspberry Pi 5 Emulator                      ║\n", "kernel")
        self.append_console("║  Starting QEMU with BCM2712 (4× Cortex-A76, 4GB RAM)         ║\n", "kernel")
        self.append_console("╚══════════════════════════════════════════════════════════════╝\n\n", "kernel")
        
        self.running = True
        self.boot_complete = False
        self.boot_start_time = time.time()
        self.progress.start(100)
        self.status_label.config(text="● Booting LobsterOS...", foreground=self.colors["yellow"])
        self.launch_button.config(state=tk.DISABLED)
        self.input_entry.config(state=tk.DISABLED)
        self.send_button.config(state=tk.DISABLED)
        for btn, _ in self.quick_buttons:
            btn.config(state=tk.DISABLED)
            
        # Start QEMU in background thread
        thread = threading.Thread(target=self.run_qemu, daemon=True)
        thread.start()
        
    def run_qemu(self):
        """Run QEMU process (in background thread)"""
        try:
            self.process = subprocess.Popen(
                [QEMU_BIN] + QEMU_ARGS,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0
            )
            
            # Read output line by line
            if self.process.stdout:
                while self.running and self.process.poll() is None:
                    try:
                        line = self.process.stdout.readline()
                        if line:
                            decoded = line.decode('utf-8', errors='replace')
                            self.output_queue.put(decoded)
                            
                            # Detect boot completion - look for shell prompt
                            if not self.boot_complete and "lobster>" in decoded:
                                # Check if it's a fresh prompt (not command echo)
                                lines = decoded.strip().split('\n')
                                prompt_found = False
                                for l in lines:
                                    if l.strip().startswith("lobster>") and len(l.strip()) > 9:
                                        # This looks like a fresh prompt
                                        prompt_found = True
                                        break
                                
                                if prompt_found:
                                    time.sleep(0.3)
                                    self.output_queue.put("__BOOT_COMPLETE__")
                            # Fallback: if boot takes too long (>25 seconds), assume it's ready
                            elif not self.boot_complete and (time.time() - self.boot_start_time) > 25:
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
            
    def stop_emulator(self):
        """Stop the QEMU process"""
        if self.process and self.process.poll() is None:
            self.append_console("\n[Stopping emulator...]\n", "warning")
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                
    def restart_emulator(self):
        """Restart the emulator"""
        self.stop_emulator()
        time.sleep(0.5)
        self.launch_emulator()
        
    def on_send_command(self, event=None):
        """Send command to emulator"""
        cmd = self.input_entry.get().strip()
        if cmd and self.process and self.process.poll() is None:
            self.append_console(f"lobster> {cmd}\n", "user_input")
            try:
                if self.process.stdin:
                    self.process.stdin.write((cmd + "\n").encode())
                    self.process.stdin.flush()
                    # Add to history
                    self.command_history.append(cmd)
                    self.history_index = len(self.command_history)
            except Exception as e:
                self.append_console(f"[Send error: {e}]\n", "error")
        self.input_entry.delete(0, tk.END)
        
    def send_quick_command(self, cmd):
        """Send a quick command button"""
        if self.process and self.process.poll() is None and self.process.stdin:
            self.append_console(f"lobster> {cmd}\n", "user_input")
            try:
                self.process.stdin.write((cmd + "\n").encode())
                self.process.stdin.flush()
                self.command_history.append(cmd)
                self.history_index = len(self.command_history)
            except Exception as e:
                self.append_console(f"[Send error: {e}]\n", "error")
                
    def on_history_up(self, event):
        """Navigate command history up"""
        if self.command_history and self.history_index > 0:
            self.history_index -= 1
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, self.command_history[self.history_index])
        return "break"
        
    def on_history_down(self, event):
        """Navigate command history down"""
        if self.command_history and self.history_index < len(self.command_history) - 1:
            self.history_index += 1
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, self.command_history[self.history_index])
        elif self.history_index >= len(self.command_history) - 1:
            self.history_index = len(self.command_history)
            self.input_entry.delete(0, tk.END)
        return "break"
        
    def on_close(self):
        """Handle window close"""
        if self.running:
            if messagebox.askyesno("Confirm Exit", "Emulator is running. Stop and exit?"):
                self.stop_emulator()
                self.root.after(500, self.root.destroy)
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    app = LobsterOSLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()