"""Desktop window built with tkinter (part of the Python standard library)."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, roblox
from .app import Controller
from .config import ACTIONS, Rule, parse_asset_list
from .logbus import log

ACTION_LABELS = {
    "replace": "Replace with asset ID",
    "remove": "Remove asset",
    "file": "Use local file",
}


def _asset_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    for candidate in (base / "assets" / name, Path(__file__).resolve().parent / "assets" / name):
        if candidate.exists():
            return candidate
    return base / "assets" / name


class RuleDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, rule: Rule | None = None) -> None:
        super().__init__(parent)
        self.title("Rule")
        self.resizable(False, False)
        self.transient(parent)
        self.result: Rule | None = None
        rule = rule or Rule()

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")
        ttk.Label(frm, text="Name").grid(row=0, column=0, sticky="w")
        self.name = tk.StringVar(value=rule.name)
        ttk.Entry(frm, textvariable=self.name, width=44).grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)

        ttk.Label(frm, text="Asset IDs").grid(row=1, column=0, sticky="nw")
        self.assets = tk.Text(frm, width=44, height=4)
        self.assets.insert("1.0", " ".join(str(a) for a in rule.assets))
        self.assets.grid(row=1, column=1, columnspan=2, sticky="ew", pady=3)

        ttk.Label(frm, text="Action").grid(row=2, column=0, sticky="w")
        self.action = tk.StringVar(value=ACTION_LABELS[rule.action])
        combo = ttk.Combobox(frm, textvariable=self.action, values=[ACTION_LABELS[a] for a in ACTIONS], state="readonly")
        combo.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh())

        self.value_label = ttk.Label(frm, text="Value")
        self.value_label.grid(row=3, column=0, sticky="w")
        self.value = tk.StringVar(value=rule.value)
        self.value_entry = ttk.Entry(frm, textvariable=self.value, width=34)
        self.value_entry.grid(row=3, column=1, sticky="ew", pady=3)
        self.browse = ttk.Button(frm, text="Browse...", command=self._browse)
        self.browse.grid(row=3, column=2, padx=(4, 0))

        self.enabled = tk.BooleanVar(value=rule.enabled)
        ttk.Checkbutton(frm, text="Enabled", variable=self.enabled).grid(row=4, column=1, sticky="w", pady=3)

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=self._save).pack(side="right", padx=6)
        self._refresh()
        self.grab_set()

    def _action_key(self) -> str:
        label = self.action.get()
        return next(k for k, v in ACTION_LABELS.items() if v == label)

    def _refresh(self) -> None:
        key = self._action_key()
        state = "normal" if key != "remove" else "disabled"
        self.value_entry.configure(state=state)
        self.browse.configure(state="normal" if key == "file" else "disabled")
        self.value_label.configure(text={"replace": "New asset ID", "file": "File", "remove": "-"}[key])

    def _browse(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Replacement file")
        if path:
            self.value.set(path)

    def _save(self) -> None:
        try:
            rule = Rule(
                name=self.name.get().strip() or "Rule",
                assets=parse_asset_list(self.assets.get("1.0", "end")),
                action=self._action_key(),
                value="" if self._action_key() == "remove" else self.value.get().strip(),
                enabled=self.enabled.get(),
            )
            rule.validate()
            if rule.action == "file" and not Path(rule.value).expanduser().is_file():
                raise ValueError("that file does not exist")
        except ValueError as exc:
            messagebox.showerror("Invalid rule", str(exc), parent=self)
            return
        self.result = rule
        self.destroy()


class MainWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.ctl = Controller()
        self.lines: queue.Queue[str] = queue.Queue()
        log.subscribe(self.lines.put)

        root.title(f"{APP_NAME} {__version__}")
        root.geometry("860x620")
        root.minsize(720, 520)
        try:
            self._icon = tk.PhotoImage(file=str(_asset_path("logo.png")))
            root.iconphoto(True, self._icon)
            self._header_icon = self._icon.subsample(max(1, self._icon.width() // 48))
        except tk.TclError:
            self._header_icon = None

        outer = ttk.Frame(root, padding=10)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        if self._header_icon is not None:
            ttk.Label(header, image=self._header_icon).pack(side="left", padx=(0, 8))
        ttk.Label(header, text=APP_NAME, font=("TkDefaultFont", 16, "bold")).pack(side="left")
        self.status = ttk.Label(header, text="Proxy stopped", foreground="#b33")
        self.status.pack(side="right")

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=8)
        self.toggle_btn = ttk.Button(bar, text="Start proxy", command=self.toggle_proxy)
        self.toggle_btn.pack(side="left")
        ttk.Button(bar, text="Launch Roblox", command=self.launch).pack(side="left", padx=6)
        ttk.Button(bar, text="Clear Roblox cache", command=self.clear_cache).pack(side="left")
        ttk.Button(bar, text="Remove from Roblox", command=self.uninstall).pack(side="right")

        opts = ttk.Frame(outer)
        opts.pack(fill="x")
        s = self.ctl.config.settings
        self.clear_var = tk.BooleanVar(value=s.clear_cache_on_launch)
        self.capture_var = tk.BooleanVar(value=s.auto_capture_launches)
        ttk.Checkbutton(opts, text="Clear cache when launching Roblox", variable=self.clear_var,
                        command=self._save_opts).pack(side="left")
        ttk.Checkbutton(opts, text="Route Roblox started from the website through the proxy",
                        variable=self.capture_var, command=self._save_opts).pack(side="left", padx=12)

        rules_box = ttk.LabelFrame(outer, text="Replacement rules", padding=6)
        rules_box.pack(fill="both", expand=True, pady=8)
        cols = ("on", "name", "assets", "action", "value")
        self.tree = ttk.Treeview(rules_box, columns=cols, show="headings", height=8, selectmode="browse")
        for col, title, width in (("on", "On", 40), ("name", "Name", 150), ("assets", "Asset IDs", 200),
                                  ("action", "Action", 150), ("value", "Value", 220)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, stretch=col in ("assets", "value"))
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self.edit_rule())
        side = ttk.Frame(rules_box)
        side.pack(side="right", fill="y", padx=(6, 0))
        for text, cmd in (("Add", self.add_rule), ("Edit", self.edit_rule), ("Toggle", self.toggle_rule),
                          ("Delete", self.delete_rule)):
            ttk.Button(side, text=text, command=cmd).pack(fill="x", pady=2)

        log_box = ttk.LabelFrame(outer, text="Log", padding=6)
        log_box.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_box, height=8, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(log_box, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._render_rules()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self._pump_log)
        log.emit("App", f"{APP_NAME} {__version__} ready - nothing leaves this computer except normal Roblox traffic")

    # -- helpers ----------------------------------------------------------
    def _bg(self, fn, done=None) -> None:
        def work() -> None:
            try:
                fn()
            except Exception as exc:
                log.emit("Error", str(exc))
            if done:
                self.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _pump_log(self) -> None:
        lines = []
        while not self.lines.empty():
            lines.append(self.lines.get_nowait())
        if lines:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n".join(lines) + "\n")
            if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
                self.log_text.delete("1.0", "1000.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(200, self._pump_log)

    def _update_status(self) -> None:
        if self.ctl.running:
            self.status.configure(text=f"Proxy running on {self.ctl.proxy_url}", foreground="#2a7")
            self.toggle_btn.configure(text="Stop proxy")
        else:
            self.status.configure(text="Proxy stopped", foreground="#b33")
            self.toggle_btn.configure(text="Start proxy")

    def _save_opts(self) -> None:
        s = self.ctl.config.settings
        s.clear_cache_on_launch = self.clear_var.get()
        s.auto_capture_launches = self.capture_var.get()
        self.ctl.config.save()
        if self.ctl.running:
            if s.auto_capture_launches and (roblox.IS_WINDOWS or roblox.IS_MACOS):
                self.ctl.watcher.start()
            else:
                self.ctl.watcher.stop()

    # -- actions ----------------------------------------------------------
    def toggle_proxy(self) -> None:
        if self.ctl.running:
            self._bg(self.ctl.stop, self._update_status)
        else:
            self._bg(self.ctl.start, self._update_status)

    def launch(self) -> None:
        self._bg(self.ctl.launch_roblox, self._update_status)

    def clear_cache(self) -> None:
        self._bg(roblox.clear_cache)

    def uninstall(self) -> None:
        if messagebox.askyesno(APP_NAME, "Remove the Interceptium certificate and setting from every Roblox install?"):
            self._bg(self.ctl.remove_from_roblox)

    # -- rules ------------------------------------------------------------
    def _rules(self) -> list[Rule]:
        return list(self.ctl.config.settings.rules)

    def _render_rules(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, r in enumerate(self._rules()):
            value = Path(r.value).name if r.action == "file" else r.value
            self.tree.insert("", "end", iid=str(index), values=(
                "✔" if r.enabled else "", r.name, ", ".join(map(str, r.assets)), ACTION_LABELS[r.action], value))

    def _selected(self) -> int | None:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _commit(self, rules: list[Rule]) -> None:
        self.ctl.config.set_rules(rules)
        self._render_rules()
        log.emit("Rules", f"{sum(r.enabled for r in rules)} rule(s) active - clear the Roblox cache to apply")

    def add_rule(self) -> None:
        dlg = RuleDialog(self.root)
        self.root.wait_window(dlg)
        if dlg.result:
            self._commit(self._rules() + [dlg.result])

    def edit_rule(self) -> None:
        index = self._selected()
        if index is None:
            return
        rules = self._rules()
        dlg = RuleDialog(self.root, rules[index])
        self.root.wait_window(dlg)
        if dlg.result:
            rules[index] = dlg.result
            self._commit(rules)

    def toggle_rule(self) -> None:
        index = self._selected()
        if index is None:
            return
        rules = self._rules()
        rules[index].enabled = not rules[index].enabled
        self._commit(rules)

    def delete_rule(self) -> None:
        index = self._selected()
        if index is None:
            return
        rules = self._rules()
        del rules[index]
        self._commit(rules)

    def on_close(self) -> None:
        self.ctl.shutdown()
        self.root.destroy()


def run() -> int:
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    MainWindow(root)
    root.mainloop()
    return 0
