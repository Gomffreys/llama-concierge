import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk

CONFIG_FILENAME = "llama_concierge_config.json"

DEFAULT_MODELS_ROOT = r"C:\LM Models"
DEFAULT_SERVER_PATH = r"C:\Program Files\llama.cpp\llama-server.exe"
DEFAULT_PORT = 8080

CUSTOM_CONTEXT_LABEL = "Custom context size..."

SHARD_RE = re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)

CONTEXT_SIZES = [8192, 16384, 24576, 32768, 40960, 49152, 57344, 65536]


def _default_flag_catalog():
    catalog = []
    for size in CONTEXT_SIZES:
        catalog.append(
            {
                "id": f"context_{size}",
                "label": f"Context {size}",
                "flag": f"-c {size}",
                "description": f"Sets the context window to {size} tokens (prompt + response). Higher uses more VRAM for the KV cache.",
            }
        )
    catalog.extend(
        [
            {
                "id": "gpu_offload_all",
                "label": "Offload All Layers to GPU",
                "flag": "-ngl 999",
                "description": "Pushes every model layer onto GPU rather than splitting with CPU RAM. A high number like this just means 'all layers'.",
            },
            {
                "id": "tensor_split_even",
                "label": "Tensor Split: Even (2 GPUs)",
                "flag": "-ts 1,1",
                "description": "Splits the model evenly across two GPUs by layer count. Matches LM Studio's 'Split Evenly' option.",
            },
            {
                "id": "load_mode_mlock",
                "label": "Load Mode: Locked in RAM (no mmap)",
                "flag": "--load-mode mlock",
                "description": "Disables memory-mapping and forces the model to stay fully resident rather than being paged. Replaces the old --no-mmap + --mlock combo.",
            },
            {
                "id": "parallel_1",
                "label": "Concurrent Requests: 1",
                "flag": "--parallel 1",
                "description": "Limits the server to one request slot at a time. Matches the 'concurrent = 1' fix from LM Studio.",
            },
            {
                "id": "flash_attn_on",
                "label": "Flash Attention: On",
                "flag": "--flash-attn on",
                "description": "Enables flash attention. Required for quantized KV cache (Q4/Q8) to actually take effect.",
            },
            {
                "id": "kv_cache_k_q4",
                "label": "KV Cache (K): Q4",
                "flag": "--cache-type-k q4_0",
                "description": "Quantizes the attention K-cache to Q4, cutting VRAM use at a small quality cost.",
            },
            {
                "id": "kv_cache_v_q4",
                "label": "KV Cache (V): Q4",
                "flag": "--cache-type-v q4_0",
                "description": "Quantizes the attention V-cache to Q4. Requires Flash Attention to be enabled.",
            },
        ]
    )
    return catalog


def _default_config():
    return {
        "flag_catalog": _default_flag_catalog(),
        "active_flags": [],
        "model_templates": {},
        "models_root": DEFAULT_MODELS_ROOT,
        "server_path": DEFAULT_SERVER_PATH,
        "port": DEFAULT_PORT,
        "selected_model": None,
    }


def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
CONFIG_PATH = os.path.join(BASE_DIR, CONFIG_FILENAME)


def load_config(config_path=CONFIG_PATH):
    defaults = _default_config()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, val in defaults.items():
                data.setdefault(key, val)
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


def save_config(config, config_path=CONFIG_PATH):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def slugify(label, existing_ids):
    base = "".join(c.lower() if c.isalnum() else "_" for c in label).strip("_")
    while "__" in base:
        base = base.replace("__", "_")
    base = base or "flag"
    slug = base
    n = 2
    while slug in existing_ids:
        slug = f"{base}_{n}"
        n += 1
    return slug


def build_command(server_path, model_path, port, active_flags, catalog_by_id):
    args = [server_path, "-m", model_path, "--port", str(port)]
    for fid in active_flags:
        f = catalog_by_id.get(fid)
        if f:
            args.extend(f["flag"].split(None, 1))
    return args


def scan_models(models_root):
    models = []
    if not os.path.isdir(models_root):
        return models
    for dirpath, _dirnames, filenames in os.walk(models_root):
        for filename in filenames:
            if not filename.lower().endswith(".gguf"):
                continue
            m = SHARD_RE.match(filename)
            suffix = ""
            if m:
                part = int(m.group(2))
                if part != 1:
                    continue
                suffix = "  (multi-part model)"
            full_path = os.path.join(dirpath, filename)
            rel = os.path.relpath(full_path, models_root)
            models.append({"label": rel + suffix, "path": full_path})
    models.sort(key=lambda m: m["label"].lower())
    return models


def open_folder(path):
    if not os.path.isdir(path):
        messagebox.showerror("Folder not found", f"Couldn't find:\n{path}")
        return
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def center_over_parent(win, parent, width=None, height=None):
    win.update_idletasks()
    if width is None:
        width = win.winfo_reqwidth()
    if height is None:
        height = win.winfo_reqheight()
    parent.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    win.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
    win.update_idletasks()


class LlamaConciergeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Llama Concierge")
        self.geometry("720x820")
        self.minsize(640, 700)

        self.config_data = load_config()
        self._model_lookup = {}
        self._available_lookup = {}
        self.selected_model_path = self.config_data.get("selected_model")

        self._build_ui()
        self._rescan_models()
        self._refresh_all()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI construction ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        model_frame = ttk.LabelFrame(self, text="Model")
        model_frame.pack(fill="x", **pad)

        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(model_frame, textvariable=self.model_var, state="readonly")
        self.model_combo.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_selected)

        ttk.Button(model_frame, text="Browse...", command=self._browse_model).pack(side="left", padx=2, pady=8)
        ttk.Button(model_frame, text="Rescan", command=self._rescan_models).pack(side="left", padx=2, pady=8)
        ttk.Button(model_frame, text="Settings...", command=self._open_settings).pack(
            side="left", padx=(2, 8), pady=8
        )

        add_frame = ttk.LabelFrame(self, text="Add argument")
        add_frame.pack(fill="x", **pad)

        add_row = ttk.Frame(add_frame)
        add_row.pack(fill="x")

        self.available_var = tk.StringVar()
        self.available_combo = ttk.Combobox(add_row, textvariable=self.available_var, state="readonly")
        self.available_combo.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        self.available_combo.bind("<<ComboboxSelected>>", lambda e: self._update_description_preview())

        ttk.Button(add_row, text="Add →", command=self._add_flag).pack(side="left", padx=4, pady=8)
        ttk.Button(add_row, text="Manage Arguments...", command=self._open_manage_flags).pack(
            side="left", padx=(4, 8), pady=8
        )

        self.description_var = tk.StringVar()
        ttk.Label(
            add_frame, textvariable=self.description_var, wraplength=680, justify="left", foreground="#555555"
        ).pack(fill="x", padx=8, pady=(0, 8))

        active_frame = ttk.LabelFrame(self, text="Active arguments (execution order)")
        active_frame.pack(fill="both", expand=True, **pad)

        list_row = ttk.Frame(active_frame)
        list_row.pack(fill="both", expand=True, padx=8, pady=8)

        self.active_listbox = tk.Listbox(list_row, selectmode="browse", exportselection=False, height=6)
        self.active_listbox.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_row, orient="vertical", command=self.active_listbox.yview)
        scrollbar.pack(side="left", fill="y")
        self.active_listbox.config(yscrollcommand=scrollbar.set)

        btn_col = ttk.Frame(list_row)
        btn_col.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(btn_col, text="Move Up", command=lambda: self._move_active(-1)).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="Move Down", command=lambda: self._move_active(1)).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="Remove", command=self._remove_active).pack(fill="x", pady=(12, 2))
        ttk.Button(btn_col, text="About", command=self._show_about).pack(side="bottom", fill="x", pady=2)

        preview_frame = ttk.LabelFrame(self, text="Command preview")
        preview_frame.pack(fill="x", **pad)
        self.preview_var = tk.StringVar()
        ttk.Label(preview_frame, textvariable=self.preview_var, wraplength=680, justify="left").pack(
            fill="x", padx=8, pady=8
        )

        template_frame = ttk.LabelFrame(self, text="Model Templates")
        template_frame.pack(fill="x", **pad)

        self.template_var = tk.StringVar()
        self.template_combo = ttk.Combobox(template_frame, textvariable=self.template_var, state="readonly")
        self.template_combo.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=8)

        ttk.Button(template_frame, text="Load", command=self._load_template).pack(side="left", padx=2, pady=8)
        ttk.Button(template_frame, text="Make Model Template", command=self._make_model_template).pack(
            side="left", padx=2, pady=8
        )
        ttk.Button(template_frame, text="Delete", command=self._delete_template).pack(
            side="left", padx=(2, 8), pady=8
        )

        launch_frame = ttk.Frame(self)
        launch_frame.pack(fill="x", padx=8, pady=(0, 6))
        launch_btn = tk.Button(
            launch_frame,
            text="Launch llama-server",
            command=self._launch,
            bg="#2e7d32",
            fg="white",
            activebackground="#256428",
            activeforeground="white",
            font=("Segoe UI", 12, "bold"),
            height=2,
        )
        launch_btn.pack(fill="x")

        folder_frame = ttk.Frame(self)
        folder_frame.pack(fill="x", padx=8, pady=(0, 10))
        folder_frame.columnconfigure(0, weight=1)
        folder_frame.columnconfigure(1, weight=1)
        ttk.Button(folder_frame, text="Open Models Folder", command=self._open_models_folder).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(folder_frame, text="Open llama.cpp Folder", command=self._open_server_folder).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

    # ---------- data helpers ----------
    def _catalog_by_id(self):
        return {f["id"]: f for f in self.config_data["flag_catalog"]}

    def _refresh_available_dropdown(self):
        catalog = self.config_data["flag_catalog"]
        active_ids = set(self.config_data["active_flags"])
        available = [f for f in catalog if f["id"] not in active_ids]
        self._available_lookup = {f["label"]: f["id"] for f in available}
        values = list(self._available_lookup.keys()) + [CUSTOM_CONTEXT_LABEL]
        self.available_combo["values"] = values
        if self.available_var.get() not in values:
            self.available_var.set(values[0] if values else "")
        self._update_description_preview()

    def _update_description_preview(self):
        label = self.available_var.get()
        if label == CUSTOM_CONTEXT_LABEL:
            self.description_var.set("Type in any context length not covered by the presets above.")
            return
        fid = self._available_lookup.get(label)
        catalog = self._catalog_by_id()
        f = catalog.get(fid)
        self.description_var.set(f.get("description", "") if f else "")

    def _refresh_active_list(self):
        self.active_listbox.delete(0, "end")
        catalog = self._catalog_by_id()
        for fid in self.config_data["active_flags"]:
            f = catalog.get(fid)
            if f:
                self.active_listbox.insert("end", f"{f['label']}   ({f['flag']})")

    def _refresh_template_dropdown(self):
        names = list(self.config_data["model_templates"].keys())
        self.template_combo["values"] = names
        if self.template_var.get() not in names:
            self.template_var.set(names[0] if names else "")

    def _update_command_preview(self):
        catalog = self._catalog_by_id()
        server_path = self.config_data.get("server_path") or DEFAULT_SERVER_PATH
        port = self.config_data.get("port", DEFAULT_PORT)
        model_path = self.selected_model_path or "<no model selected>"
        parts = build_command(server_path, model_path, port, self.config_data["active_flags"], catalog)
        self.preview_var.set(" ".join(parts))

    def _refresh_all(self):
        self._refresh_available_dropdown()
        self._refresh_active_list()
        self._refresh_template_dropdown()
        self._update_command_preview()

    # ---------- model handling ----------
    def _rescan_models(self):
        models_root = self.config_data.get("models_root") or DEFAULT_MODELS_ROOT
        models = scan_models(models_root)
        self._model_lookup = {m["label"]: m["path"] for m in models}
        labels = list(self._model_lookup.keys())
        self.model_combo["values"] = labels

        current_label = None
        for label, path in self._model_lookup.items():
            if path == self.selected_model_path:
                current_label = label
                break
        if current_label:
            self.model_var.set(current_label)
        elif labels:
            self.model_var.set(labels[0])
            self.selected_model_path = self._model_lookup[labels[0]]
        else:
            self.model_var.set("")
            self.selected_model_path = None
        self._update_command_preview()

    def _on_model_selected(self, _event=None):
        label = self.model_var.get()
        self.selected_model_path = self._model_lookup.get(label)
        self.config_data["selected_model"] = self.selected_model_path
        self._update_command_preview()
        save_config(self.config_data)

    def _browse_model(self):
        chosen = filedialog.askopenfilename(
            title="Locate a .gguf model file", filetypes=[("GGUF model", "*.gguf"), ("All files", "*.*")]
        )
        if not chosen:
            return
        label = os.path.basename(chosen) + "  (picked manually)"
        self._model_lookup[label] = chosen
        values = list(self.model_combo["values"]) + [label]
        self.model_combo["values"] = values
        self.model_var.set(label)
        self.selected_model_path = chosen
        self.config_data["selected_model"] = chosen
        self._update_command_preview()
        save_config(self.config_data)

    def _open_settings(self):
        SettingsDialog(self, self.config_data, on_change=self._on_settings_changed)

    def _on_settings_changed(self):
        self._rescan_models()
        self._refresh_all()
        save_config(self.config_data)

    # ---------- actions ----------
    def _add_flag(self):
        label = self.available_var.get()
        if label == CUSTOM_CONTEXT_LABEL:
            n = simpledialog.askinteger(
                "Custom context size", "Context size in tokens:", parent=self, minvalue=1, maxvalue=10_000_000
            )
            if not n:
                return
            flag_text = f"-c {n}"
            existing = next((f for f in self.config_data["flag_catalog"] if f["flag"] == flag_text), None)
            if existing:
                fid = existing["id"]
            else:
                existing_ids = {f["id"] for f in self.config_data["flag_catalog"]}
                fid = slugify(f"context_{n}_custom", existing_ids)
                self.config_data["flag_catalog"].append(
                    {
                        "id": fid,
                        "label": f"Context {n} (custom)",
                        "flag": flag_text,
                        "description": f"Sets the context window to {n} tokens (prompt + response).",
                    }
                )
            self.config_data["active_flags"].append(fid)
            self._refresh_all()
            save_config(self.config_data)
            return

        fid = self._available_lookup.get(label)
        if not fid:
            return
        self.config_data["active_flags"].append(fid)
        self._refresh_all()
        save_config(self.config_data)

    def _remove_active(self):
        sel = self.active_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        del self.config_data["active_flags"][idx]
        self._refresh_all()
        save_config(self.config_data)

    def _move_active(self, direction):
        sel = self.active_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        new_idx = idx + direction
        flags = self.config_data["active_flags"]
        if 0 <= new_idx < len(flags):
            flags[idx], flags[new_idx] = flags[new_idx], flags[idx]
            self._refresh_active_list()
            self._update_command_preview()
            self.active_listbox.selection_set(new_idx)
            save_config(self.config_data)

    def _load_template(self):
        name = self.template_var.get()
        if not name:
            return
        template = self.config_data["model_templates"].get(name)
        if template is None:
            return
        catalog_ids = {f["id"] for f in self.config_data["flag_catalog"]}
        self.config_data["active_flags"] = [fid for fid in template.get("flags", []) if fid in catalog_ids]

        model_path = template.get("model")
        if model_path:
            label = next((l for l, p in self._model_lookup.items() if p == model_path), None)
            if not label:
                label = os.path.basename(model_path) + "  (from template)"
                self._model_lookup[label] = model_path
                self.model_combo["values"] = list(self.model_combo["values"]) + [label]
            self.model_var.set(label)
            self.selected_model_path = model_path
            self.config_data["selected_model"] = model_path

        self._refresh_all()
        save_config(self.config_data)

    def _make_model_template(self):
        if not self.selected_model_path:
            messagebox.showwarning("No model selected", "Select a model before saving a template.", parent=self)
            return
        name = simpledialog.askstring("Make Model Template", "Template name:", parent=self)
        if not name:
            return
        self.config_data["model_templates"][name] = {
            "model": self.selected_model_path,
            "flags": list(self.config_data["active_flags"]),
        }
        self.template_var.set(name)
        self._refresh_template_dropdown()
        save_config(self.config_data)

    def _delete_template(self):
        name = self.template_var.get()
        if not name or name not in self.config_data["model_templates"]:
            return
        if messagebox.askyesno("Delete template", f"Delete template '{name}'?"):
            del self.config_data["model_templates"][name]
            self.template_var.set("")
            self._refresh_template_dropdown()
            save_config(self.config_data)

    def _open_manage_flags(self):
        ManageFlagsDialog(self, self.config_data, on_change=self._refresh_all)

    def _show_about(self):
        messagebox.showinfo(
            "About",
            "Llama Concierge\n"
            "\n"
            "This software was authored using Claude AI\n"
            'and is shared "As is". Use at your own risk!\n'
            "\n"
            "Only download Llama Concierge from:\n"
            "https://github.com/Gomffreys/llama-concierge",
            parent=self,
        )

    def _open_models_folder(self):
        open_folder(self.config_data.get("models_root") or DEFAULT_MODELS_ROOT)

    def _open_server_folder(self):
        server_path = self.config_data.get("server_path") or DEFAULT_SERVER_PATH
        open_folder(os.path.dirname(server_path))

    def _launch(self):
        server_path = self.config_data.get("server_path") or DEFAULT_SERVER_PATH
        if not os.path.isfile(server_path):
            chosen = filedialog.askopenfilename(
                title="Locate llama-server.exe",
                filetypes=[("llama-server.exe", "llama-server.exe"), ("All files", "*.*")],
            )
            if not chosen:
                return
            server_path = chosen
            self.config_data["server_path"] = server_path
            save_config(self.config_data)

        if not self.selected_model_path or not os.path.isfile(self.selected_model_path):
            messagebox.showerror("No model selected", "Pick a model before launching.")
            return

        port = int(self.config_data.get("port", DEFAULT_PORT))
        args = build_command(
            server_path, self.selected_model_path, port, self.config_data["active_flags"], self._catalog_by_id()
        )
        save_config(self.config_data)

        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        try:
            subprocess.Popen(args, cwd=os.path.dirname(server_path), creationflags=creationflags)
        except OSError as e:
            messagebox.showerror("Launch failed", str(e))
            return

        threading.Thread(target=self._wait_and_open_browser, args=(port,), daemon=True).start()

    def _wait_and_open_browser(self, port, timeout=600):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass
                webbrowser.open(f"http://127.0.0.1:{port}")
                return
            except OSError:
                time.sleep(1)
        self.after(
            0,
            lambda: messagebox.showwarning(
                "Still loading",
                f"The server hasn't responded after 10 minutes.\n"
                f"Once it's ready, open http://127.0.0.1:{port} manually.",
            ),
        )

    def _on_close(self):
        save_config(self.config_data)
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, config_data, on_change):
        super().__init__(parent)
        self.title("Settings")
        self.config_data = config_data
        self.on_change = on_change
        self.transient(parent)
        self.grab_set()

        form = ttk.Frame(self)
        form.pack(fill="both", expand=True, padx=10, pady=10)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Models folder:").grid(row=0, column=0, sticky="w", padx=4, pady=6)
        self.models_root_var = tk.StringVar(value=config_data.get("models_root") or DEFAULT_MODELS_ROOT)
        ttk.Entry(form, textvariable=self.models_root_var).grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        ttk.Button(form, text="Browse...", command=self._browse_models_root).grid(row=0, column=2, padx=4, pady=6)

        ttk.Label(form, text="llama-server.exe:").grid(row=1, column=0, sticky="w", padx=4, pady=6)
        self.server_path_var = tk.StringVar(value=config_data.get("server_path") or DEFAULT_SERVER_PATH)
        ttk.Entry(form, textvariable=self.server_path_var).grid(row=1, column=1, sticky="ew", padx=4, pady=6)
        ttk.Button(form, text="Browse...", command=self._browse_server_path).grid(row=1, column=2, padx=4, pady=6)

        ttk.Label(form, text="Port:").grid(row=2, column=0, sticky="w", padx=4, pady=6)
        self.port_var = tk.StringVar(value=str(config_data.get("port", DEFAULT_PORT)))
        ttk.Entry(form, textvariable=self.port_var, width=10).grid(row=2, column=1, sticky="w", padx=4, pady=6)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 10))
        ttk.Button(btn_row, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=4)

        center_over_parent(self, parent, width=520)

    def _browse_models_root(self):
        chosen = filedialog.askdirectory(title="Select your models folder")
        if chosen:
            self.models_root_var.set(chosen)

    def _browse_server_path(self):
        chosen = filedialog.askopenfilename(
            title="Locate llama-server.exe",
            filetypes=[("llama-server.exe", "llama-server.exe"), ("All files", "*.*")],
        )
        if chosen:
            self.server_path_var.set(chosen)

    def _save(self):
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid port", "Port must be a number.", parent=self)
            return
        self.config_data["models_root"] = self.models_root_var.get().strip() or DEFAULT_MODELS_ROOT
        self.config_data["server_path"] = self.server_path_var.get().strip() or DEFAULT_SERVER_PATH
        self.config_data["port"] = port
        self.on_change()
        self.destroy()


class ManageFlagsDialog(tk.Toplevel):
    def __init__(self, parent, config_data, on_change):
        super().__init__(parent)
        self.title("Manage Arguments")
        self.config_data = config_data
        self.on_change = on_change
        self.transient(parent)
        self.grab_set()

        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.listbox = tk.Listbox(list_frame)
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._update_description_preview())
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="left", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

        list_btn_col = ttk.Frame(list_frame)
        list_btn_col.pack(side="left", padx=(8, 0), anchor="n")
        ttk.Button(list_btn_col, text="Edit", command=self._edit_selected).pack(fill="x", pady=(0, 4))
        ttk.Button(list_btn_col, text="Delete", command=self._delete_selected).pack(fill="x")

        self.description_var = tk.StringVar()
        ttk.Label(
            self, textvariable=self.description_var, wraplength=460, justify="left", foreground="#555555"
        ).pack(fill="x", padx=10, pady=(0, 10))

        form = ttk.LabelFrame(self, text="Add new argument")
        form.pack(fill="x", padx=10, pady=(0, 10))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Label:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.label_entry = ttk.Entry(form)
        self.label_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(form, text="Flag text:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.flag_entry = ttk.Entry(form)
        self.flag_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(form, text="Description:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.desc_entry = ttk.Entry(form)
        self.desc_entry.grid(row=2, column=1, sticky="ew", padx=8, pady=4)

        ttk.Button(form, text="Add to catalog", command=self._add_new).grid(
            row=3, column=0, columnspan=2, pady=(4, 8)
        )

        ttk.Button(self, text="Close", command=self._close).pack(pady=(0, 10))

        self._refresh_list()
        center_over_parent(self, parent, width=520, height=520)

    def _refresh_list(self):
        self.listbox.delete(0, "end")
        for f in self.config_data["flag_catalog"]:
            self.listbox.insert("end", f"{f['label']}   ({f['flag']})")
        self._update_description_preview()

    def _update_description_preview(self):
        sel = self.listbox.curselection()
        if not sel:
            self.description_var.set("")
            return
        f = self.config_data["flag_catalog"][sel[0]]
        self.description_var.set(f.get("description", ""))

    def _add_new(self):
        label = self.label_entry.get().strip()
        flag = self.flag_entry.get().strip()
        description = self.desc_entry.get().strip()
        if not label or not flag:
            messagebox.showwarning("Missing info", "Both label and flag text are required.", parent=self)
            return
        existing_ids = {f["id"] for f in self.config_data["flag_catalog"]}
        fid = slugify(label, existing_ids)
        self.config_data["flag_catalog"].append(
            {"id": fid, "label": label, "flag": flag, "description": description}
        )
        self.label_entry.delete(0, "end")
        self.flag_entry.delete(0, "end")
        self.desc_entry.delete(0, "end")
        self._refresh_list()
        self.on_change()
        save_config(self.config_data)

    def _edit_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        flag_entry = self.config_data["flag_catalog"][idx]
        EditFlagDialog(self, flag_entry, on_save=self._on_edit_saved)

    def _on_edit_saved(self):
        self._refresh_list()
        self.on_change()
        save_config(self.config_data)

    def _delete_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        removed = self.config_data["flag_catalog"][idx]
        if messagebox.askyesno(
            "Delete argument",
            f"Delete '{removed['label']}' from the catalog?\n"
            "(Also removes it from the active list and any templates.)",
            parent=self,
        ):
            fid = removed["id"]
            del self.config_data["flag_catalog"][idx]
            self.config_data["active_flags"] = [x for x in self.config_data["active_flags"] if x != fid]
            for template in self.config_data["model_templates"].values():
                if fid in template.get("flags", []):
                    template["flags"].remove(fid)
            self._refresh_list()
            self.on_change()
            save_config(self.config_data)

    def _close(self):
        save_config(self.config_data)
        self.destroy()


class EditFlagDialog(tk.Toplevel):
    def __init__(self, parent, flag_entry, on_save):
        super().__init__(parent)
        self.title("Edit Argument")
        self.flag_entry = flag_entry
        self.on_save = on_save
        self.transient(parent)
        self.grab_set()

        form = ttk.Frame(self)
        form.pack(fill="both", expand=True, padx=10, pady=10)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Label:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.label_var = tk.StringVar(value=flag_entry["label"])
        ttk.Entry(form, textvariable=self.label_var).grid(row=0, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(form, text="Flag text:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.flag_var = tk.StringVar(value=flag_entry["flag"])
        ttk.Entry(form, textvariable=self.flag_var).grid(row=1, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(form, text="Description:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.desc_var = tk.StringVar(value=flag_entry.get("description", ""))
        ttk.Entry(form, textvariable=self.desc_var).grid(row=2, column=1, sticky="ew", padx=8, pady=4)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 10))
        ttk.Button(btn_row, text="Change", command=self._change).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=4)

        center_over_parent(self, parent, width=440)

    def _change(self):
        label = self.label_var.get().strip()
        flag = self.flag_var.get().strip()
        description = self.desc_var.get().strip()
        if not label or not flag:
            messagebox.showwarning("Missing info", "Both label and flag text are required.", parent=self)
            return
        self.flag_entry["label"] = label
        self.flag_entry["flag"] = flag
        self.flag_entry["description"] = description
        self.on_save()
        self.destroy()


def main():
    app = LlamaConciergeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
