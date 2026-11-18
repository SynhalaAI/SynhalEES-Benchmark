"""Pure-stdlib desktop studio for the SynhalEES Benchmark (tkinter / ttk).

Organized around guided, step-by-step workflows with sidebar navigation:
    - Overview      : project health snapshot & quick launch cards
    - Kaggle Sync   : Step 1 Discover -> Step 2 Run -> Step 3 Pull -> Step 4 Submit & Build
    - Local Eval    : Step 1 Configure & Run -> Step 2 Compare -> Step 3 Publish
    - Explorer      : browse all 15 Pillars (eval counts) & Model Registry
    - Site & CI     : verification gates, rebuild leaderboard/logos, live preview

Launch via:
    synhalees gui [--tab overview|run|kaggle|site|explorer]
    python -m synhalees gui
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from synhalees import kaggle_ops
from synhalees.data import list_pillars, load_pillar
from synhalees.kaggle_ops import (
    ROOT,
    RUNS,
    expand_import_tasks,
    gather_coverage,
    parse_kaggle_models_output,
    run_kaggle_captured,
)
from synhalees.registry import canonical_model_slug, default_registry


# ---------------------------------------------------------------------------
# Pure-Python helpers (testable without opening a Tk window)
# ---------------------------------------------------------------------------

TAB_KEYS = ("overview", "kaggle", "run", "explorer", "site")

_MODALITY_FLAG_MAP = {
    "Text only (15 tasks · ~145 MB)": "text",
    "Vision only": "vision",
    "Audio only": "audio",
    "Vision + Audio (~730 MB)": "vision|audio",
    "All 17 tasks (T+V+A)": "all",
}


@dataclass(frozen=True)
class DashboardSnapshot:
    """Read-only summary of dataset, submissions, site sync, and local runs."""

    pillar_count: int
    text_items: int
    vision_items: int
    audio_items: int
    submission_models: int
    submission_vendors: int
    latest_date: str
    logos_ok: bool
    site_data_ok: bool
    kaggle_models: int
    kaggle_full_tva: int
    local_runs: list[str]


def _all_model_slugs() -> list[str]:
    from synhalees.cli import _scan_submissions

    subs = _scan_submissions()
    try:
        kg_cov, _, _ = gather_coverage(check_remote=False)
    except Exception:
        kg_cov = {}
    return sorted(
        set(default_registry.display_names.keys()) | set(subs.keys()) | set(kg_cov.keys())
    )


def collect_dashboard_snapshot() -> DashboardSnapshot:
    """Gather fast local repository metrics for the Overview & Status cards."""
    from synhalees.cli import _scan_submissions, _site_sync_status

    pillars = list_pillars()
    text_n = vision_n = audio_n = 0
    for p in pillars:
        pd = load_pillar(p, strict=False)
        text_n += len(pd.text)
        vision_n += len(pd.vision)
        audio_n += len(pd.audio)

    subs = _scan_submissions()
    vendors = {default_registry.resolve_taxonomy(m)[0] for m in subs}
    dates = [info["date"] for info in subs.values() if info.get("date")]
    latest = max(dates) if dates else "none"
    logos_ok, site_ok = _site_sync_status()

    try:
        model_tasks, _, _ = gather_coverage(check_remote=False)
    except Exception:
        model_tasks = {}

    full_tva = sum(
        1
        for st in model_tasks.values()
        if st["text"] >= 15 and st["vision"] > 0 and st["audio"] > 0
    )

    local_runs: list[str] = []
    if RUNS.is_dir():
        for d in sorted(RUNS.iterdir()):
            if d.is_dir() and (d / "submission.csv").is_file():
                local_runs.append(d.name)

    return DashboardSnapshot(
        pillar_count=len(pillars),
        text_items=text_n,
        vision_items=vision_n,
        audio_items=audio_n,
        submission_models=len(subs),
        submission_vendors=len(vendors),
        latest_date=latest,
        logos_ok=logos_ok,
        site_data_ok=site_ok,
        kaggle_models=len(model_tasks),
        kaggle_full_tva=full_tva,
        local_runs=local_runs,
    )


def build_run_args(
    *,
    model: str,
    judge: str = "",
    modality: str = "text",
    pillars: str = "",
    fresh: bool = False,
) -> list[str]:
    """Build `synhalees run ...` CLI arguments from form values."""
    args = ["run", "--model", model.strip()]
    if judge.strip():
        args.extend(["--judge", judge.strip()])
    if modality and modality != "text":
        args.extend(["--modality", modality])
    if pillars.strip() and pillars.strip().lower() != "all":
        args.extend(["--pillars", pillars.strip()])
    if fresh:
        args.append("--fresh")
    return args


def build_kaggle_args(
    action: str,
    *,
    task: str = "all",
    model: str = "",
    modality: str = "",
    force: bool = False,
    dry_run: bool = False,
    build: bool = False,
    no_remote: bool = False,
    colab: bool = False,
) -> list[str]:
    """Build `synhalees kaggle <action> ...` CLI arguments from form values."""
    act = action.strip().lower()
    args = ["kaggle", act]
    if act == "gen":
        if dry_run:
            args.append("--dry-run")
        return args
    models = [m.strip() for m in re.split(r"[,\s]+", model.strip()) if m.strip()]

    if act == "coverage":
        if no_remote:
            args.append("--no-remote")
        if models:
            args.extend(["--provider", models[0]])
        return args
    if act in ("push", "status", "pull", "submit", "publish"):
        args.append(task.strip() or "all")
    elif act in ("run", "logs"):
        args.append(task.strip() or "synhalees-audio")

    if act in ("run", "logs", "pull", "status") and models:
        for m in models:
            args.extend(["-m", m])
    if act == "pull" and modality.strip():
        resolved_mod = _MODALITY_FLAG_MAP.get(modality.strip(), modality.strip())
        args.extend(["--modality", resolved_mod])
    if act == "pull" and force:
        args.append("--force")
    if act == "pull" and colab:
        args.append("--colab")
    if act in ("pull", "submit") and dry_run:
        args.append("--dry-run")
    if act == "submit" and build:
        args.append("--build")
    return args


def format_cmd_preview(args: list[str]) -> str:
    """Format a human-readable `synhalees ...` preview string."""
    return "synhalees " + " ".join(shlex.quote(a) for a in args)


TOKENS_FILE = Path.home() / ".synhalees" / "tokens.json"


def load_saved_tokens(tokens_file: Path | None = None, *, overwrite: bool = False) -> int:
    """Load persisted tokens from ~/.synhalees/tokens.json into os.environ."""
    path = tokens_file or TOKENS_FILE
    try:
        if not path.is_file():
            return 0
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    loaded = 0
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            if overwrite or not os.environ.get(k):
                os.environ[k] = v.strip()
                loaded += 1
    return loaded


def kaggle_auth_status() -> str:
    """Return a concise summary of active Kaggle credentials."""
    if os.environ.get("KAGGLE_API_TOKEN"):
        return "KAGGLE_API_TOKEN set"
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return f"Env ({os.environ['KAGGLE_USERNAME']})"
    cfg = Path.home() / ".kaggle" / "kaggle.json"
    try:
        if cfg.is_file():
            return "~/.kaggle/kaggle.json"
    except OSError:
        pass
    return "Not configured"


def apply_tokens(
    *,
    kaggle_token: str = "",
    kaggle_username: str = "",
    kaggle_key: str = "",
    save_kaggle_json: bool = False,
    api_keys: dict[str, str] | None = None,
    kaggle_dir: Path | None = None,
    persist: bool = True,
    tokens_file: Path | None = None,
) -> list[str]:
    """Apply Kaggle and API tokens to os.environ and persist them to ~/.synhalees/tokens.json.

    Supports pasting either a raw `KAGGLE_API_TOKEN` (e.g. `KGAT_...`) or a full
    `{"username": "...", "key": "..."}` JSON snippet into *kaggle_token*.
    Returns human-readable notes describing what was updated.
    """
    notes: list[str] = []
    tok = kaggle_token.strip()
    user = kaggle_username.strip()
    key = kaggle_key.strip()

    if tok.startswith("{") and tok.endswith("}"):
        try:
            parsed = json.loads(tok)
            if isinstance(parsed, dict):
                user = str(parsed.get("username") or user).strip()
                key = str(parsed.get("key") or key).strip()
                tok = ""
        except json.JSONDecodeError:
            pass

    if tok:
        os.environ["KAGGLE_API_TOKEN"] = tok
        notes.append("KAGGLE_API_TOKEN updated")
    if user:
        os.environ["KAGGLE_USERNAME"] = user
    if key:
        os.environ["KAGGLE_KEY"] = key
    if user and key:
        notes.append(f"KAGGLE_USERNAME/KAGGLE_KEY set for '{user}'")
        if save_kaggle_json:
            target_dir = kaggle_dir or (Path.home() / ".kaggle")
            target_dir.mkdir(parents=True, exist_ok=True)
            cfg = target_dir / "kaggle.json"
            cfg.write_text(json.dumps({"username": user, "key": key}, indent=2) + "\n", encoding="utf-8")
            notes.append(f"Saved {cfg}")

    if tok or (user and key):
        kaggle_ops._MODELS_CACHE = None
        kaggle_ops._STATUS_MODELS_CACHE.clear()

    for env_name, val in (api_keys or {}).items():
        clean = val.strip()
        if clean:
            os.environ[env_name] = clean
            notes.append(f"{env_name} updated")

    if persist:
        tfile = tokens_file or TOKENS_FILE
        existing: dict[str, str] = {}
        if tfile.is_file():
            try:
                raw = json.loads(tfile.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v.strip()}
            except Exception:
                existing = {}

        if tok:
            existing["KAGGLE_API_TOKEN"] = tok
        elif not kaggle_token.strip() and "KAGGLE_API_TOKEN" in existing and not os.environ.get("KAGGLE_API_TOKEN"):
            existing.pop("KAGGLE_API_TOKEN", None)
        if user:
            existing["KAGGLE_USERNAME"] = user
        elif "KAGGLE_USERNAME" in existing and not os.environ.get("KAGGLE_USERNAME"):
            existing.pop("KAGGLE_USERNAME", None)
        if key:
            existing["KAGGLE_KEY"] = key
        elif "KAGGLE_KEY" in existing and not os.environ.get("KAGGLE_KEY"):
            existing.pop("KAGGLE_KEY", None)
        for env_name, val in (api_keys or {}).items():
            clean = val.strip()
            if clean:
                existing[env_name] = clean
            else:
                existing.pop(env_name, None)
                os.environ.pop(env_name, None)

        if existing:
            tfile.parent.mkdir(parents=True, exist_ok=True)
            tfile.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
            try:
                if os.name != "nt":
                    tfile.chmod(0o600)
            except Exception:
                pass
            notes.append(f"Persisted tokens to {tfile}")
        elif tfile.is_file():
            tfile.unlink()

    return notes


# ---------------------------------------------------------------------------
# HiDPI & Palette
# ---------------------------------------------------------------------------


def _enable_hidpi() -> None:
    """Enable crisp HiDPI rendering on Windows before creating the Tk root."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        try:
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except Exception:
            pass
        shcore = getattr(ctypes.windll, "shcore", None)
        if shcore is not None:
            for level in (2, 1):
                try:
                    shcore.SetProcessDpiAwareness(level)
                    return
                except Exception:
                    pass
        user32.SetProcessDPIAware()
    except Exception:
        pass


PALETTE = {
    "bg": "#141824",
    "sidebar": "#191e2e",
    "surface": "#1d2336",
    "card": "#252c44",
    "card_hover": "#2d3654",
    "border": "#364060",
    "text": "#eef1f8",
    "muted": "#9aa3ba",
    "accent": "#e04545",
    "accent_hover": "#f15a5a",
    "ok": "#2fb368",
    "warn": "#f5b301",
    "info": "#56a8ff",
    "step_badge": "#303a5a",
    "console_bg": "#0e111b",
}


# ---------------------------------------------------------------------------
# Desktop Studio Application (tkinter / ttk)
# ---------------------------------------------------------------------------


class SynhaleesGUI:
    """Streamlined SynhalEES Benchmark Studio with sidebar workflows."""

    def __init__(self, root: Any, *, initial_tab: str = "overview") -> None:
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        load_saved_tokens()
        try:
            self._scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            self._scale = 1.0

        self.root.title("SynhalEES Benchmark Studio")
        sw = max(800, root.winfo_screenwidth())
        sh = max(600, root.winfo_screenheight())
        win_w = min(self._px(1180), int(sw * 0.93))
        win_h = min(self._px(780), int(sh * 0.91))
        min_w = min(self._px(920), int(sw * 0.85))
        min_h = min(self._px(620), int(sh * 0.80))
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(min_w, min_h)
        self.root.configure(bg=PALETTE["bg"])

        self._proc: subprocess.Popen[str] | None = None
        self._proc_lock = threading.Lock()
        self._log_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._last_args: list[str] = ["status"]
        self._active_page = "overview"
        self._nav_buttons: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}
        self._console_visible = False

        self._configure_styles()
        self._build_topbar()
        self._build_shell(scrolledtext)
        self.select_page(initial_tab if initial_tab in TAB_KEYS else "overview")
        self.refresh_all()
        self.root.after(70, self._drain_queue)

    def _px(self, value: int) -> int:
        return max(1, round(value * self._scale))

    def _configure_styles(self) -> None:
        s = self.ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except Exception:
            pass

        bg = PALETTE["bg"]
        sb = PALETTE["sidebar"]
        surf = PALETTE["surface"]
        card = PALETTE["card"]
        fg = PALETTE["text"]
        muted = PALETTE["muted"]
        acc = PALETTE["accent"]
        bdr = PALETTE["border"]

        s.configure(".", background=bg, foreground=fg, font=("Segoe UI", 10))
        s.configure("TFrame", background=bg)
        s.configure("Sidebar.TFrame", background=sb)
        s.configure("Surface.TFrame", background=surf)
        s.configure("Card.TFrame", background=card, relief="flat")

        s.configure("TLabel", background=bg, foreground=fg)
        s.configure("Muted.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        s.configure("PageTitle.TLabel", background=bg, foreground=fg, font=("Segoe UI", 14, "bold"))
        s.configure("PageSub.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        s.configure("CardTitle.TLabel", background=card, foreground=muted, font=("Segoe UI", 8, "bold"))
        s.configure("CardValue.TLabel", background=card, foreground=fg, font=("Segoe UI", 13, "bold"))
        s.configure("CardSub.TLabel", background=card, foreground=muted, font=("Segoe UI", 9))
        s.configure("StepTitle.TLabel", background=card, foreground=fg, font=("Segoe UI", 11, "bold"))
        s.configure("StepHint.TLabel", background=card, foreground=muted, font=("Segoe UI", 9))
        s.configure("CardText.TLabel", background=card, foreground=fg, font=("Segoe UI", 9))
        s.configure("Surface.TLabel", background=surf, foreground=fg)

        s.configure(
            "Accent.TButton",
            background=acc,
            foreground="#ffffff",
            borderwidth=0,
            padding=[14, 7],
            font=("Segoe UI", 9, "bold"),
        )
        s.map(
            "Accent.TButton",
            background=[("active", PALETTE["accent_hover"]), ("disabled", bdr)],
            foreground=[("disabled", muted)],
        )

        s.configure(
            "Ghost.TButton",
            background=surf,
            foreground=fg,
            borderwidth=1,
            bordercolor=bdr,
            padding=[11, 6],
            font=("Segoe UI", 9),
        )
        s.map(
            "Ghost.TButton",
            background=[("active", PALETTE["card_hover"]), ("disabled", sb)],
            foreground=[("disabled", muted)],
        )

        s.configure(
            "TEntry",
            fieldbackground=PALETTE["console_bg"],
            foreground=fg,
            insertcolor=fg,
            bordercolor=bdr,
            padding=5,
        )
        s.configure(
            "TCombobox",
            fieldbackground=PALETTE["console_bg"],
            background=surf,
            foreground=fg,
            arrowcolor=fg,
            bordercolor=bdr,
            padding=4,
        )
        s.map(
            "TCombobox",
            background=[("active", PALETTE["card_hover"]), ("readonly", surf)],
            fieldbackground=[("readonly", PALETTE["console_bg"])],
            selectbackground=[("readonly", card)],
            selectforeground=[("readonly", fg)],
        )
        s.configure("TCheckbutton", background=card, foreground=fg, font=("Segoe UI", 9))
        s.map("TCheckbutton", background=[("active", card)], foreground=[("active", "#ffffff")])

        s.configure(
            "TScrollbar",
            background=card,
            troughcolor=sb,
            arrowcolor=fg,
            bordercolor=bdr,
        )
        s.map("TScrollbar", background=[("active", PALETTE["card_hover"])])

        s.configure(
            "Treeview",
            background=PALETTE["console_bg"],
            fieldbackground=PALETTE["console_bg"],
            foreground=fg,
            borderwidth=0,
            rowheight=self._px(26),
            font=("Segoe UI", 9),
        )
        s.configure(
            "Treeview.Heading",
            background=card,
            foreground=fg,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )
        s.map(
            "Treeview.Heading",
            background=[("active", PALETTE["card_hover"]), ("pressed", bdr)],
            foreground=[("active", "#ffffff")],
        )
        s.map("Treeview", background=[("selected", "#354066")], foreground=[("selected", "#ffffff")])

    # ------------------------------------------------------------------ layout

    def _build_topbar(self) -> None:
        tk, ttk = self.tk, self.ttk
        bar = tk.Frame(self.root, bg=PALETTE["sidebar"], highlightthickness=1, highlightbackground=PALETTE["border"])
        bar.pack(fill="x")

        inner = tk.Frame(bar, bg=PALETTE["sidebar"])
        inner.pack(fill="x", padx=self._px(16), pady=self._px(10))

        left = tk.Frame(inner, bg=PALETTE["sidebar"])
        left.pack(side="left")
        tk.Label(
            left,
            text="SynhalEES",
            bg=PALETTE["sidebar"],
            fg=PALETTE["accent"],
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            left,
            text=" Studio",
            bg=PALETTE["sidebar"],
            fg=PALETTE["text"],
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            left,
            text="  ·  Guided Benchmark Control Center",
            bg=PALETTE["sidebar"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(6, 0))

        right = tk.Frame(inner, bg=PALETTE["sidebar"])
        right.pack(side="right")
        self.status_badge = tk.Label(
            right,
            text="IDLE",
            bg=PALETTE["card"],
            fg=PALETTE["ok"],
            font=("Segoe UI", 9, "bold"),
            padx=self._px(10),
            pady=self._px(4),
        )
        self.status_badge.pack(side="left", padx=(0, 8))
        ttk.Button(right, text="Tokens & Keys", style="Ghost.TButton", command=self._open_tokens_dialog).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(right, text="Refresh", style="Ghost.TButton", command=self.refresh_all).pack(side="left", padx=(0, 6))
        ttk.Button(
            right,
            text="Open Leaderboard",
            style="Ghost.TButton",
            command=self._open_leaderboard,
        ).pack(side="left")

    def _build_shell(self, scrolledtext: Any) -> None:
        tk, ttk = self.tk, self.ttk

        # Bottom Live Output Console Drawer (packed first with side="bottom" so
        # hiding the log collapses it to a slim 1-line footer taking zero extra height)
        self.console_wrap = tk.Frame(
            self.root,
            bg=PALETTE["surface"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
        )
        self.console_wrap.pack(side="bottom", fill="x")

        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)

        # Left Navigation Rail
        sidebar = tk.Frame(
            body,
            bg=PALETTE["sidebar"],
            width=self._px(220),
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(
            sidebar,
            text="WORKFLOWS",
            bg=PALETTE["sidebar"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=self._px(16), pady=(self._px(14), self._px(6)))

        nav_items = [
            ("overview", "1. Overview", "Project status & quick actions"),
            ("kaggle", "2. Kaggle Sync", "Discover, run, pull & import"),
            ("run", "3. Local Eval", "Run API / Ollama & publish"),
            ("explorer", "4. Explorer", "15 pillars & model registry"),
            ("site", "5. Site & CI", "Build data, logos & verify"),
        ]
        for key, title, sub in nav_items:
            btn = tk.Frame(sidebar, bg=PALETTE["sidebar"], cursor="hand2")
            btn.pack(fill="x", padx=self._px(8), pady=self._px(2))
            accent_bar = tk.Frame(btn, bg=PALETTE["sidebar"], width=self._px(3))
            accent_bar.pack(side="left", fill="y")
            txt_wrap = tk.Frame(btn, bg=PALETTE["sidebar"])
            txt_wrap.pack(side="left", fill="x", expand=True, padx=self._px(10), pady=self._px(8))
            lbl_t = tk.Label(
                txt_wrap,
                text=title,
                bg=PALETTE["sidebar"],
                fg=PALETTE["text"],
                font=("Segoe UI", 10, "bold"),
                anchor="w",
            )
            lbl_t.pack(fill="x")
            lbl_s = tk.Label(
                txt_wrap,
                text=sub,
                bg=PALETTE["sidebar"],
                fg=PALETTE["muted"],
                font=("Segoe UI", 8),
                anchor="w",
            )
            lbl_s.pack(fill="x")

            for w in (btn, accent_bar, txt_wrap, lbl_t, lbl_s):
                w.bind("<Button-1>", lambda _e, k=key: self.select_page(k))

            self._nav_buttons[key] = (btn, accent_bar, txt_wrap, lbl_t, lbl_s)

        # Workspace container (right)
        self.content_host = ttk.Frame(body)
        self.content_host.pack(side="left", fill="both", expand=True)

        self._pages["overview"] = self._make_scrollable_page("overview")
        self._pages["kaggle"] = self._make_scrollable_page("kaggle")
        self._pages["run"] = self._make_scrollable_page("run")
        self._pages["explorer"] = self._make_scrollable_page("explorer")
        self._pages["site"] = self._make_scrollable_page("site")

        self._build_overview_page(self._pages["overview"].inner)
        self._build_kaggle_page(self._pages["kaggle"].inner)
        self._build_run_page(self._pages["run"].inner)
        self._build_explorer_page(self._pages["explorer"].inner)
        self._build_site_page(self._pages["site"].inner)

        # Bottom Live Output Console controls (packed inside console_wrap created above)

        cbar = tk.Frame(self.console_wrap, bg=PALETTE["surface"])
        cbar.pack(fill="x", padx=self._px(12), pady=self._px(6))
        tk.Label(
            cbar,
            text="ACTIVITY LOG",
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")

        self.cmd_label_var = tk.StringVar(value="synhalees status")
        tk.Label(
            cbar,
            textvariable=self.cmd_label_var,
            bg=PALETTE["surface"],
            fg=PALETTE["info"],
            font=("Consolas", 9),
        ).pack(side="left", padx=(self._px(12), 0))

        self.stop_btn = ttk.Button(
            cbar,
            text="Stop",
            style="Ghost.TButton",
            command=self.stop_command,
            state="disabled",
        )
        self.stop_btn.pack(side="right", padx=(6, 0))
        ttk.Button(cbar, text="Clear", style="Ghost.TButton", command=self._clear_log).pack(side="right", padx=(6, 0))
        ttk.Button(cbar, text="Copy Cmd", style="Ghost.TButton", command=self._copy_last_cmd).pack(side="right", padx=(6, 0))
        self.toggle_log_btn = ttk.Button(cbar, text="Show Log", style="Ghost.TButton", command=self._toggle_console)
        self.toggle_log_btn.pack(side="right")

        self.console_body = tk.Frame(self.console_wrap, bg=PALETTE["console_bg"])
        # Hidden by default so workflow content gets full vertical space.

        self.console = scrolledtext.ScrolledText(
            self.console_body,
            height=8,
            bg=PALETTE["console_bg"],
            fg=PALETTE["text"],
            insertbackground=PALETTE["text"],
            font=("Consolas", 9),
            relief="flat",
            borderwidth=0,
            wrap="word",
            state="disabled",
        )
        self.console.pack(fill="both", expand=True)
        self.console.tag_config("cmd", foreground=PALETTE["info"], font=("Consolas", 9, "bold"))
        self.console.tag_config("ok", foreground=PALETTE["ok"])
        self.console.tag_config("warn", foreground=PALETTE["warn"])
        self.console.tag_config("err", foreground=PALETTE["accent"])
        self.console.tag_config("muted", foreground=PALETTE["muted"])

    def _make_scrollable_page(self, key: str) -> Any:
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.content_host)
        canvas = tk.Canvas(outer, bg=PALETTE["bg"], highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=(self._px(20), self._px(16), self._px(20), self._px(20)))
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)

        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _sync_region(_e=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(e: Any) -> None:
            canvas.itemconfigure(win_id, width=e.width)

        def _on_wheel(e: Any) -> None:
            try:
                delta = int(-1 * (e.delta / 120)) if e.delta else 0
                if delta:
                    canvas.yview_scroll(delta, "units")
            except Exception:
                pass

        inner.bind("<Configure>", _sync_region)
        canvas.bind("<Configure>", _sync_width)
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        outer.inner = inner  # type: ignore[attr-defined]
        outer._key = key     # type: ignore[attr-defined]
        return outer

    def select_page(self, key: str) -> None:
        if key not in self._pages:
            key = "overview"
        self._active_page = key
        for k, page in self._pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for k, (btn, bar, wrap, lt, ls) in self._nav_buttons.items():
            active = k == key
            bg = PALETTE["card"] if active else PALETTE["sidebar"]
            btn.configure(bg=bg)
            wrap.configure(bg=bg)
            lt.configure(bg=bg, fg="#ffffff" if active else PALETTE["text"])
            ls.configure(bg=bg)
            bar.configure(bg=PALETTE["accent"] if active else bg)

    def _toggle_console(self) -> None:
        self._console_visible = not self._console_visible
        if self._console_visible:
            self.console_body.pack(fill="both", expand=True, padx=self._px(10), pady=(0, self._px(8)))
            self.toggle_log_btn.configure(text="Hide Log")
        else:
            self.console_body.pack_forget()
            self.toggle_log_btn.configure(text="Show Log")

    # ------------------------------------------------------------------ Card helpers

    def _page_header(self, parent: Any, title: str, subtitle: str) -> None:
        ttk = self.ttk
        ttk.Label(parent, text=title, style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(parent, text=subtitle, style="PageSub.TLabel").pack(anchor="w", pady=(2, self._px(12)))

    def _step_card(self, parent: Any, step_num: str, title: str, hint: str) -> Any:
        tk, ttk = self.tk, self.ttk
        frame = tk.Frame(
            parent,
            bg=PALETTE["card"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            padx=self._px(14),
            pady=self._px(12),
        )
        frame.pack(fill="x", pady=(0, self._px(12)))

        head = tk.Frame(frame, bg=PALETTE["card"])
        head.pack(fill="x", pady=(0, self._px(8)))
        if step_num:
            tk.Label(
                head,
                text=step_num,
                bg=PALETTE["step_badge"],
                fg=PALETTE["info"],
                font=("Segoe UI", 8, "bold"),
                padx=self._px(8),
                pady=self._px(2),
            ).pack(side="left", padx=(0, self._px(10)))
        ttk.Label(head, text=title, style="StepTitle.TLabel").pack(side="left")
        if hint:
            ttk.Label(frame, text=hint, style="StepHint.TLabel", wraplength=self._px(780), justify="left").pack(
                anchor="w", pady=(0, self._px(8))
            )
        return frame

    # ------------------------------------------------------------------ 1. Overview

    def _build_overview_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Project Overview",
            "Live snapshot of your dataset, published leaderboard submissions, Kaggle archive, and site sync.",
        )

        # 4 Summary KPI Cards
        cards_row = ttk.Frame(parent)
        cards_row.pack(fill="x", pady=(0, self._px(14)))
        for col in range(4):
            cards_row.columnconfigure(col, weight=1, uniform="kpi")

        self.card_vars: dict[str, tuple[Any, Any, Any]] = {}
        specs = [
            ("dataset", "DATASET"),
            ("submissions", "PUBLISHED MODELS"),
            ("site", "SITE SYNC"),
            ("kaggle", "KAGGLE SLIM ARCHIVE"),
        ]
        for idx, (key, title) in enumerate(specs):
            card = tk.Frame(
                cards_row,
                bg=PALETTE["card"],
                highlightthickness=1,
                highlightbackground=PALETTE["border"],
                padx=self._px(12),
                pady=self._px(10),
            )
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else self._px(8), 0))
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
            v_main = tk.StringVar(value="—")
            v_sub = tk.StringVar(value="")
            v_lbl = tk.Label(
                card,
                textvariable=v_main,
                bg=PALETTE["card"],
                fg=PALETTE["text"],
                font=("Segoe UI", 13, "bold"),
                anchor="w",
            )
            v_lbl.pack(anchor="w", pady=(4, 2))
            ttk.Label(card, textvariable=v_sub, style="CardSub.TLabel").pack(anchor="w")
            self.card_vars[key] = (v_main, v_sub, v_lbl)

        # Workflow Launchpad Cards
        c_kaggle = self._step_card(
            parent,
            "WORKFLOW A",
            "Sync New Models from Kaggle Benchmarks",
            "Inspect what new models ran on Kaggle, pull lightweight *.slim.csv results, import into submissions/, and rebuild the site.",
        )
        r_kg = tk.Frame(c_kaggle, bg=PALETTE["card"])
        r_kg.pack(fill="x")
        ttk.Button(
            r_kg,
            text="Open Kaggle Sync Workflow  ->",
            style="Accent.TButton",
            command=lambda: self.select_page("kaggle"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r_kg,
            text="Check Missing Server Runs Now",
            style="Ghost.TButton",
            command=lambda: self.run_cli(["kaggle", "coverage"]),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r_kg,
            text="Discover Kaggle Models",
            style="Ghost.TButton",
            command=self._discover_kaggle_models,
        ).pack(side="left")

        c_local = self._step_card(
            parent,
            "WORKFLOW B",
            "Run & Publish Local Model Evaluation (API / Ollama)",
            "Evaluate a local Ollama or API model checkpoint-safely across pillars/modalities, compare runs, and promote to submissions/.",
        )
        r_loc = tk.Frame(c_local, bg=PALETTE["card"])
        r_loc.pack(fill="x")
        ttk.Button(
            r_loc,
            text="Open Local Eval Workflow  ->",
            style="Ghost.TButton",
            command=lambda: self.select_page("run"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r_loc,
            text="Explore 15 Pillars & Models",
            style="Ghost.TButton",
            command=lambda: self.select_page("explorer"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r_loc,
            text="Verify All Gates (check)",
            style="Ghost.TButton",
            command=lambda: self.run_cli(["check"]),
        ).pack(side="left")

    # ------------------------------------------------------------------ 2. Kaggle Sync

    def _build_kaggle_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Kaggle Benchmarks Workflow",
            "Follow Steps 1 to 4 in order. Hover nothing — every action shows its exact CLI command in the preview bar.",
        )

        self.kg_task_var = tk.StringVar(value="all")
        self.kg_model_var = tk.StringVar(value="")
        self.kg_mod_label_var = tk.StringVar(value="Text only (15 tasks · ~145 MB)")
        self.kg_force_var = tk.BooleanVar(value=False)
        self.kg_dry_var = tk.BooleanVar(value=False)
        self.kg_cov_filter_var = tk.StringVar(value="All")

        # Split Kaggle Sync into 2 columns so Steps 1-4 stay visible without scrolling
        cols = ttk.Frame(parent)
        cols.pack(fill="both", expand=True)
        cols.columnconfigure(0, weight=3)
        cols.columnconfigure(1, weight=2)

        left_col = ttk.Frame(cols)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, self._px(12)))
        right_col = ttk.Frame(cols)
        right_col.grid(row=0, column=1, sticky="nsew")

        # STEP 1: Discover (compact)
        s1 = self._step_card(
            left_col,
            "STEP 1",
            "Check Kaggle Server for New or Missing Models",
            "Check which models on Kaggle are missing locally or newly added on Kaggle.",
        )
        r1 = tk.Frame(s1, bg=PALETTE["card"])
        r1.pack(fill="x", pady=(0, self._px(6)))
        ttk.Button(
            r1,
            text="Check Missing Runs",
            style="Accent.TButton",
            command=lambda: self.run_cli(build_kaggle_args("coverage", model=self.kg_model_var.get())),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r1,
            text="List New Models",
            style="Ghost.TButton",
            command=self._discover_kaggle_models,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            r1,
            text="Task Status",
            style="Ghost.TButton",
            command=lambda: self.run_cli(build_kaggle_args("status", task=self.kg_task_var.get(), model=self.kg_model_var.get())),
        ).pack(side="left", padx=(0, 6))
        self.kg_token_btn = ttk.Button(
            r1,
            text="Token",
            style="Ghost.TButton",
            command=self._open_tokens_dialog,
        )
        self._sync_token_button()

        self.kg_discover_status_var = tk.StringVar(
            value="Click 'List New Models' to scan Kaggle. Click any table row on the right to select a model."
        )
        tk.Label(
            s1,
            textvariable=self.kg_discover_status_var,
            bg=PALETTE["card"],
            fg=PALETTE["info"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x")

        # RIGHT COLUMN: New Models card (shown when discovered) + Local Slim Coverage card
        self.kg_new_frame = tk.Frame(
            right_col,
            bg=PALETTE["card"],
            highlightthickness=1,
            highlightbackground=PALETTE["ok"],
            padx=self._px(12),
            pady=self._px(10),
        )
        new_hdr = tk.Frame(self.kg_new_frame, bg=PALETTE["card"])
        new_hdr.pack(fill="x", pady=(0, self._px(4)))
        tk.Label(
            new_hdr,
            text="New Models on Kaggle (Ctrl/Shift-click for multi):",
            bg=PALETTE["card"],
            fg=PALETTE["ok"],
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        ttk.Button(
            new_hdr,
            text="Select All",
            style="Ghost.TButton",
            command=self._select_all_new_kaggle_models,
        ).pack(side="right")
        self.kg_new_tree = ttk.Treeview(
            self.kg_new_frame,
            columns=("slug", "canonical", "status"),
            show="headings",
            height=4,
            selectmode="extended",
        )
        for col, lbl, w in (
            ("slug", "Kaggle Slug", 150),
            ("canonical", "Canonical", 140),
            ("status", "Status", 90),
        ):
            self.kg_new_tree.heading(col, text=lbl)
            self.kg_new_tree.column(col, width=self._px(w), anchor="w" if col != "status" else "center")
        self.kg_new_tree.pack(fill="x")
        self._bind_toggle_tree(self.kg_new_tree)
        self.kg_new_tree.bind("<<TreeviewSelect>>", self._on_select_new_kaggle_model)

        self.kg_cov_card = tk.Frame(
            right_col,
            bg=PALETTE["card"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            padx=self._px(12),
            pady=self._px(10),
        )
        self.kg_cov_card.pack(fill="both", expand=True)
        self.kg_cov_bar = tk.Frame(self.kg_cov_card, bg=PALETTE["card"])
        self.kg_cov_bar.pack(fill="x", pady=(0, self._px(6)))
        tk.Label(
            self.kg_cov_bar,
            text="Local Slim Coverage",
            bg=PALETTE["card"],
            fg=PALETTE["text"],
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        cov_filt = ttk.Combobox(
            self.kg_cov_bar,
            textvariable=self.kg_cov_filter_var,
            values=["All", "Incomplete only", "Complete 15/15 T+V+A"],
            state="readonly",
            width=20,
        )
        cov_filt.pack(side="right")
        cov_filt.bind("<<ComboboxSelected>>", lambda _e: self._populate_kaggle_table())

        self.kg_tree = ttk.Treeview(
            self.kg_cov_card,
            columns=("model", "text", "vision", "audio"),
            show="headings",
            height=14,
        )
        for col, lbl, w in (
            ("model", "Model Slug", 165),
            ("text", "Text", 60),
            ("vision", "Vision", 65),
            ("audio", "Audio", 65),
        ):
            self.kg_tree.heading(col, text=lbl)
            self.kg_tree.column(col, width=self._px(w), anchor="w" if col == "model" else "center")
        self.kg_tree.pack(fill="both", expand=True)
        self._bind_toggle_tree(self.kg_tree)
        self.kg_tree.bind("<<TreeviewSelect>>", self._on_select_cov_model)

        # STEP 2: Pull Results (in left_col — always visible!)
        s2 = self._step_card(
            left_col,
            "STEP 2",
            "Download Finished Kaggle Results into *.slim.csv",
            "Downloads to .temp/, merges scores per (model, pillar, modality), and auto-cleans .temp/.",
        )
        g2 = tk.Frame(s2, bg=PALETTE["card"])
        g2.pack(fill="x", pady=(0, self._px(8)))
        g2.columnconfigure(1, weight=1)
        g2.columnconfigure(3, weight=1)

        task_choices = ["all", "synhalees-vision", "synhalees-audio", *expand_import_tasks("text")]
        ttk.Label(g2, text="Scope / Task", style="CardText.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(g2, textvariable=self.kg_task_var, values=task_choices).grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=4)

        ttk.Label(g2, text="Modality Preset", style="CardText.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(
            g2,
            textvariable=self.kg_mod_label_var,
            values=list(_MODALITY_FLAG_MAP.keys()),
            state="readonly",
        ).grid(row=0, column=3, sticky="ew", pady=4)

        ttk.Label(g2, text="Model Filter (-m)", style="CardText.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        mf_wrap = tk.Frame(g2, bg=PALETTE["card"])
        mf_wrap.grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=4)
        ttk.Combobox(mf_wrap, textvariable=self.kg_model_var, values=["", *_all_model_slugs()]).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            mf_wrap,
            text="Clear",
            style="Ghost.TButton",
            command=self._clear_selected_kaggle_models,
        ).pack(side="left", padx=(4, 0))

        f2 = tk.Frame(g2, bg=PALETTE["card"])
        f2.grid(row=1, column=2, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(f2, text="Dry run (preview only)", variable=self.kg_dry_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(f2, text="Force re-download cached", variable=self.kg_force_var).pack(side="left")

        r2 = tk.Frame(s2, bg=PALETTE["card"])
        r2.pack(fill="x")
        ttk.Button(
            r2,
            text="Pull Results from Kaggle",
            style="Accent.TButton",
            command=lambda: self.run_cli(
                build_kaggle_args(
                    "pull",
                    task=self.kg_task_var.get(),
                    model=self.kg_model_var.get(),
                    modality=self.kg_mod_label_var.get(),
                    force=self.kg_force_var.get(),
                    dry_run=self.kg_dry_var.get(),
                )
            ),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r2,
            text="Pull via Google Colab (Slim Only)",
            style="Ghost.TButton",
            command=lambda: self.run_cli(
                build_kaggle_args(
                    "pull",
                    task=self.kg_task_var.get(),
                    model=self.kg_model_var.get(),
                    modality=self.kg_mod_label_var.get(),
                    force=self.kg_force_var.get(),
                    dry_run=self.kg_dry_var.get(),
                    colab=True,
                )
            ),
        ).pack(side="left")

        # STEP 3: Submit & Rebuild Site
        s3 = self._step_card(
            left_col,
            "STEP 3",
            "Import Slim CSVs into submissions/ & Rebuild Leaderboard Site",
            "Writes canonical scorecards to submissions/<vendor>/<family>/<model>.csv and regenerates docs/assets/data/*.",
        )
        r3 = tk.Frame(s3, bg=PALETTE["card"])
        r3.pack(fill="x")
        ttk.Button(
            r3,
            text="Import & Rebuild Site (submit --build)",
            style="Accent.TButton",
            command=lambda: self.run_cli(
                build_kaggle_args(
                    "submit",
                    task=self.kg_task_var.get(),
                    dry_run=self.kg_dry_var.get(),
                    build=True,
                )
            ),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r3,
            text="Verify All Gates (check)",
            style="Ghost.TButton",
            command=lambda: self.run_cli(["check"]),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r3,
            text="Open Leaderboard Preview",
            style="Ghost.TButton",
            command=self._open_leaderboard,
        ).pack(side="left")

        # STEP 4: Advanced / Trigger New Server Runs
        s4 = self._step_card(
            left_col,
            "STEP 4 · OPTIONAL",
            "Push Task Scripts or Start New Evaluations on Kaggle Servers",
            "Use this only when updating benchmark tasks or triggering a new `-m <model>` evaluation directly on Kaggle.",
        )
        r4 = tk.Frame(s4, bg=PALETTE["card"])
        r4.pack(fill="x")
        for label, act in (
            ("1. Gen Tasks", "gen"),
            ("2. Push Tasks", "push"),
            ("3. Run Model", "run"),
            ("Fetch Logs", "logs"),
            ("Publish on Kaggle.com", "publish"),
        ):
            ttk.Button(
                r4,
                text=label,
                style="Ghost.TButton",
                command=lambda a=act: self.run_cli(
                    build_kaggle_args(
                        a,
                        task=self.kg_task_var.get(),
                        model=self.kg_model_var.get(),
                        dry_run=self.kg_dry_var.get(),
                    )
                ),
            ).pack(side="left", padx=(0, 8))

    # ------------------------------------------------------------------ 3. Local Eval

    def _build_run_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Local Evaluation Workflow (API / Ollama)",
            "Run crash-safe evaluations locally into runs/<model-slug>/, compare across runs, and promote finished runs.",
        )

        model_specs = [f"{default_registry.resolve_taxonomy(m)[0]}:{m}" for m in _all_model_slugs()]
        self.run_model_var = tk.StringVar(value="ollama:llama3.1:8b")
        self.run_judge_var = tk.StringVar(value="")
        self.run_modality_var = tk.StringVar(value="text")
        self.run_pillars_var = tk.StringVar(value="all")
        self.run_fresh_var = tk.BooleanVar(value=False)

        s1 = self._step_card(
            parent,
            "STEP 1",
            "Configure & Start Local Benchmark Run",
            "Results checkpoint automatically per item so interrupted runs resume cleanly unless 'Fresh run' is checked.",
        )
        form = tk.Frame(s1, bg=PALETTE["card"])
        form.pack(fill="x", pady=(0, self._px(10)))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        ttk.Label(form, text="Model spec", style="CardText.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(form, textvariable=self.run_model_var, values=model_specs).grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=5)

        ttk.Label(form, text="Judge spec (opt)", style="CardText.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(form, textvariable=self.run_judge_var, values=["", *model_specs]).grid(row=0, column=3, sticky="ew", pady=5)

        ttk.Label(form, text="Modality", style="CardText.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(
            form,
            textvariable=self.run_modality_var,
            values=["text", "vision", "audio", "all"],
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=5)

        ttk.Label(form, text="Pillars", style="CardText.TLabel").grid(row=1, column=2, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(form, textvariable=self.run_pillars_var, values=["all", *list_pillars()]).grid(row=1, column=3, sticky="ew", pady=5)

        ttk.Checkbutton(form, text="Fresh run (discard prior checkpoint)", variable=self.run_fresh_var).grid(
            row=2, column=1, sticky="w", pady=(4, 0)
        )

        self.run_preview_var = tk.StringVar()
        for var in (self.run_model_var, self.run_judge_var, self.run_modality_var, self.run_pillars_var, self.run_fresh_var):
            var.trace_add("write", lambda *_: self._update_run_preview())
        self._update_run_preview()

        prev = tk.Frame(s1, bg=PALETTE["console_bg"], padx=self._px(10), pady=self._px(7))
        prev.pack(fill="x", pady=(0, self._px(10)))
        tk.Label(
            prev,
            textvariable=self.run_preview_var,
            bg=PALETTE["console_bg"],
            fg=PALETTE["info"],
            font=("Consolas", 9),
            anchor="w",
        ).pack(fill="x")

        r1 = tk.Frame(s1, bg=PALETTE["card"])
        r1.pack(fill="x")
        ttk.Button(r1, text="Start Local Run", style="Accent.TButton", command=self._start_local_run).pack(side="left")

        # STEP 2: Compare
        s2 = self._step_card(
            parent,
            "STEP 2",
            "Compare Completed Local Runs",
            "Aggregates runs/*/submission.csv into runs/all_submissions.csv and prints a pillar comparison matrix.",
        )
        ttk.Button(s2, text="Compare Local Runs (compare)", style="Ghost.TButton", command=lambda: self.run_cli(["compare"])).pack(
            anchor="w"
        )

        # STEP 3: Publish
        s3 = self._step_card(
            parent,
            "STEP 3",
            "Promote Finished Run to submissions/ & Rebuild Site",
            "Copies runs/<slug>/submission.csv into submissions/<vendor>/<family>/<slug>.csv and rebuilds docs/assets/data/.",
        )
        prow = tk.Frame(s3, bg=PALETTE["card"])
        prow.pack(fill="x")
        self.pub_slug_var = tk.StringVar(value="")
        self.pub_combo = ttk.Combobox(prow, textvariable=self.pub_slug_var, values=[])
        self.pub_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.pub_dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(prow, text="Dry run", variable=self.pub_dry_var).pack(side="left", padx=(0, 10))
        ttk.Button(prow, text="Publish & Rebuild Site", style="Accent.TButton", command=self._publish_selected_run).pack(side="left")

    # ------------------------------------------------------------------ 4. Explorer

    def _build_explorer_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Dataset & Model Registry Explorer",
            "Inspect all 15 Sinhala cultural pillars and all models registered in synhalees/models.json or submissions/.",
        )

        # Pillars Card
        p_card = self._step_card(parent, "PILLARS", "The 15 Core Pillars", "")
        self.pillars_tree = ttk.Treeview(
            p_card,
            columns=("slug", "text", "vision", "audio"),
            show="headings",
            height=7,
        )
        for col, lbl, w in (
            ("slug", "Pillar Slug", 260),
            ("text", "Text", 90),
            ("vision", "Vision", 90),
            ("audio", "Audio", 90),
        ):
            self.pillars_tree.heading(col, text=lbl)
            self.pillars_tree.column(col, width=self._px(w), anchor="w" if col == "slug" else "center")
        self.pillars_tree.pack(fill="x")

        # Models Card
        m_card = self._step_card(parent, "REGISTRY", "Registered & Published Models", "")
        fbar = tk.Frame(m_card, bg=PALETTE["card"])
        fbar.pack(fill="x", pady=(0, self._px(6)))
        tk.Label(fbar, text="Filter by model or vendor:", bg=PALETTE["card"], fg=PALETTE["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.model_search_var = tk.StringVar()
        self.model_search_var.trace_add("write", lambda *_: self._populate_models_table())
        ttk.Entry(fbar, textvariable=self.model_search_var, width=28).pack(side="left", padx=(8, 0))

        self.models_tree = ttk.Treeview(
            m_card,
            columns=("model", "provider", "modalities"),
            show="headings",
            height=9,
        )
        for col, lbl, w in (
            ("model", "Canonical Slug", 260),
            ("provider", "Provider", 130),
            ("modalities", "Published T / V / A", 140),
        ):
            self.models_tree.heading(col, text=lbl)
            self.models_tree.column(col, width=self._px(w), anchor="w" if col != "modalities" else "center")
        self.models_tree.pack(fill="x")

    # ------------------------------------------------------------------ 5. Site & CI

    def _build_site_page(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self._page_header(
            parent,
            "Static Site & Verification Gates",
            "Keep docs/assets/data/* and docs/assets/logo-data.js in sync with submissions/ and run CI checks before committing.",
        )

        self.site_sync_detail_var = tk.StringVar(value="")
        c1 = self._step_card(
            parent,
            "STEP 1",
            "Run Pre-Commit Verification Gates",
            "Checks logo-data.js checksum, leaderboard JSON sync, all 15 dataset CSV schemas, and Python syntax compilation.",
        )
        tk.Label(
            c1,
            textvariable=self.site_sync_detail_var,
            bg=PALETTE["card"],
            fg=PALETTE["info"],
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, self._px(8)))
        ttk.Button(c1, text="Run All Checks (synhalees check)", style="Accent.TButton", command=lambda: self.run_cli(["check"])).pack(
            anchor="w"
        )

        c2 = self._step_card(
            parent,
            "STEP 2",
            "Regenerate Generated Site Files",
            "Never hand-edit docs/assets/data/* or docs/assets/logo-data.js — regenerate them directly here.",
        )
        r2 = tk.Frame(c2, bg=PALETTE["card"])
        r2.pack(fill="x")
        ttk.Button(r2, text="Rebuild Leaderboard Data (site build)", style="Ghost.TButton", command=lambda: self.run_cli(["site", "build"])).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(r2, text="Rebuild Logo Data (site logos)", style="Ghost.TButton", command=lambda: self.run_cli(["site", "logos"])).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(r2, text="Preview docs/index.html", style="Ghost.TButton", command=self._open_leaderboard).pack(side="left")

    # ------------------------------------------------------------------ Data refresh

    def refresh_all(self) -> None:
        snap = collect_dashboard_snapshot()
        self.card_vars["dataset"][0].set(f"{snap.pillar_count} pillars")
        self.card_vars["dataset"][1].set(f"T:{snap.text_items}  V:{snap.vision_items}  A:{snap.audio_items}")
        self.card_vars["submissions"][0].set(f"{snap.submission_models} models")
        self.card_vars["submissions"][1].set(f"{snap.submission_vendors} vendors · {snap.latest_date}")
        sync_ok = snap.logos_ok and snap.site_data_ok
        self.card_vars["site"][0].set("Synced" if sync_ok else "Needs Rebuild")
        self.card_vars["site"][2].configure(fg=PALETTE["ok"] if sync_ok else PALETTE["warn"])
        self.card_vars["site"][1].set(
            f"logos:{'OK' if snap.logos_ok else 'STALE'}  data:{'OK' if snap.site_data_ok else 'STALE'}"
        )
        self.card_vars["kaggle"][0].set(f"{snap.kaggle_models} slim models")
        self.card_vars["kaggle"][1].set(f"{snap.kaggle_full_tva}/{snap.kaggle_models} full T+V+A")
        self.site_sync_detail_var.set(
            f"Current Sync: logo-data.js={'OK' if snap.logos_ok else 'STALE'}  |  docs/assets/data={'OK' if snap.site_data_ok else 'STALE'}"
        )

        self.pub_combo["values"] = snap.local_runs
        if snap.local_runs and not self.pub_slug_var.get():
            self.pub_slug_var.set(snap.local_runs[0])

        self._populate_pillars_table()
        self._populate_models_table()
        self._populate_kaggle_table()
        self._sync_token_button()

    def _sync_token_button(self) -> None:
        if not hasattr(self, "kg_token_btn"):
            return
        if kaggle_auth_status() == "Not configured":
            if not self.kg_token_btn.winfo_ismapped():
                self.kg_token_btn.pack(side="left")
        else:
            self.kg_token_btn.pack_forget()

    def _populate_pillars_table(self) -> None:
        self.pillars_tree.delete(*self.pillars_tree.get_children())
        for p in list_pillars():
            pd = load_pillar(p, strict=False)
            self.pillars_tree.insert("", "end", values=(p, len(pd.text), len(pd.vision), len(pd.audio)))

    def _populate_models_table(self) -> None:
        from synhalees.cli import _scan_submissions

        self.models_tree.delete(*self.models_tree.get_children())
        subs = _scan_submissions()
        q = self.model_search_var.get().strip().lower() if hasattr(self, "model_search_var") else ""
        for m in _all_model_slugs():
            prov = default_registry.resolve_taxonomy(m)[0]
            if q and q not in m.lower() and q not in prov.lower():
                continue
            mods = subs.get(m, {}).get("modalities", set())
            tva = "".join(k[0].upper() if k in mods else "-" for k in ("text", "vision", "audio"))
            self.models_tree.insert("", "end", values=(m, prov, tva))

    def _populate_kaggle_table(self) -> None:
        self.kg_tree.delete(*self.kg_tree.get_children())
        mode = self.kg_cov_filter_var.get() if hasattr(self, "kg_cov_filter_var") else "All"
        try:
            model_tasks, _, _ = gather_coverage(check_remote=False)
        except Exception:
            model_tasks = {}
        for m in sorted(model_tasks):
            st = model_tasks[m]
            is_complete = st["text"] >= 15 and st["vision"] > 0 and st["audio"] > 0
            if mode == "Incomplete only" and is_complete:
                continue
            if mode == "Complete 15/15 T+V+A" and not is_complete:
                continue
            self.kg_tree.insert(
                "",
                "end",
                values=(
                    m,
                    f"{st['text']}/15",
                    "DONE" if st["vision"] > 0 else "missing",
                    "DONE" if st["audio"] > 0 else "missing",
                ),
            )

    # ------------------------------------------------------------------ Actions

    def _update_run_preview(self) -> None:
        args = build_run_args(
            model=self.run_model_var.get(),
            judge=self.run_judge_var.get(),
            modality=self.run_modality_var.get(),
            pillars=self.run_pillars_var.get(),
            fresh=self.run_fresh_var.get(),
        )
        self.run_preview_var.set(format_cmd_preview(args))

    def _start_local_run(self) -> None:
        if not self.run_model_var.get().strip():
            self._append_log("[ERR] Please enter a model spec (e.g. ollama:llama3.1:8b).\n", "err")
            return
        self._start_worker_if_free(
            lambda: self.run_cli(
                build_run_args(
                    model=self.run_model_var.get(),
                    judge=self.run_judge_var.get(),
                    modality=self.run_modality_var.get(),
                    pillars=self.run_pillars_var.get(),
                    fresh=self.run_fresh_var.get(),
                )
            )
        )

    def _start_worker_if_free(self, fn: Callable[[], None]) -> None:
        fn()

    def _publish_selected_run(self) -> None:
        slug = self.pub_slug_var.get().strip()
        if not slug:
            self._append_log("[WARN] No local run selected under runs/.\n", "warn")
            return
        args = ["site", "publish", slug]
        if self.pub_dry_var.get():
            args.append("--dry-run")
        self.run_cli(args)

    def _open_leaderboard(self) -> None:
        index_html = ROOT / "docs" / "index.html"
        if index_html.is_file():
            webbrowser.open(index_html.resolve().as_uri())
            self._append_log(f"[OK] Opened {index_html}\n", "ok")
        else:
            self._append_log(f"[ERR] Missing {index_html}\n", "err")

    def _open_tokens_dialog(self) -> None:
        """Open a modal dialog to configure Kaggle tokens & Local Eval API keys."""
        tk, ttk = self.tk, self.ttk
        dlg = tk.Toplevel(self.root)
        dlg.title("Tokens & API Keys")
        dlg.configure(bg=PALETTE["surface"])
        dlg.transient(self.root)
        dlg.grab_set()
        w, h = self._px(540), self._px(490)
        dlg.geometry(f"{w}x{h}")
        dlg.minsize(self._px(460), self._px(430))

        wrap = tk.Frame(dlg, bg=PALETTE["surface"], padx=self._px(18), pady=self._px(14))
        wrap.pack(fill="both", expand=True)

        tk.Label(
            wrap,
            text="Tokens & API Keys (Saved Locally)",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            wrap,
            text=f"Kaggle Auth: {kaggle_auth_status()}  ·  Paste KAGGLE_API_TOKEN (KGAT_...) or kaggle.json",
            bg=PALETTE["surface"],
            fg=PALETTE["info"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, self._px(10)))

        show_var = tk.BooleanVar(value=False)
        k_tok_var = tk.StringVar(value=os.environ.get("KAGGLE_API_TOKEN", ""))
        k_user_var = tk.StringVar(value=os.environ.get("KAGGLE_USERNAME", ""))
        k_key_var = tk.StringVar(value=os.environ.get("KAGGLE_KEY", ""))
        save_json_var = tk.BooleanVar(value=False)

        api_vars = {
            "GEMINI_API_KEY": tk.StringVar(value=os.environ.get("GEMINI_API_KEY", "")),
            "OPENAI_API_KEY": tk.StringVar(value=os.environ.get("OPENAI_API_KEY", "")),
            "ANTHROPIC_API_KEY": tk.StringVar(value=os.environ.get("ANTHROPIC_API_KEY", "")),
            "OPENROUTER_API_KEY": tk.StringVar(value=os.environ.get("OPENROUTER_API_KEY", "")),
        }

        form = tk.Frame(wrap, bg=PALETTE["card"], padx=self._px(12), pady=self._px(10))
        form.pack(fill="x", pady=(0, self._px(10)))
        form.columnconfigure(1, weight=1)

        secret_entries: list[Any] = []

        def _row(r: int, label: str, var: Any, secret: bool = True) -> None:
            tk.Label(
                form,
                text=label,
                bg=PALETTE["card"],
                fg=PALETTE["text"],
                font=("Segoe UI", 9),
            ).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=4)
            ent = ttk.Entry(form, textvariable=var, show="*" if secret else "")
            ent.grid(row=r, column=1, sticky="ew", pady=4)
            if secret:
                secret_entries.append(ent)

        _row(0, "KAGGLE_API_TOKEN", k_tok_var, secret=True)
        _row(1, "KAGGLE_USERNAME (opt)", k_user_var, secret=False)
        _row(2, "KAGGLE_KEY (opt)", k_key_var, secret=True)
        ttk.Checkbutton(
            form,
            text="Also save username + key to ~/.kaggle/kaggle.json",
            variable=save_json_var,
        ).grid(row=3, column=1, sticky="w", pady=(2, 6))

        _row(4, "GEMINI_API_KEY", api_vars["GEMINI_API_KEY"], secret=True)
        _row(5, "OPENAI_API_KEY", api_vars["OPENAI_API_KEY"], secret=True)
        _row(6, "ANTHROPIC_API_KEY", api_vars["ANTHROPIC_API_KEY"], secret=True)
        _row(7, "OPENROUTER_API_KEY", api_vars["OPENROUTER_API_KEY"], secret=True)

        def _toggle_show() -> None:
            mask = "" if show_var.get() else "*"
            for ent in secret_entries:
                ent.configure(show=mask)

        btns = tk.Frame(wrap, bg=PALETTE["surface"])
        btns.pack(fill="x")
        tk.Checkbutton(
            btns,
            text="Show tokens",
            variable=show_var,
            command=_toggle_show,
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            selectcolor=PALETTE["console_bg"],
            activebackground=PALETTE["surface"],
            activeforeground=PALETTE["text"],
        ).pack(side="left")

        def _save_and_close() -> None:
            if not k_tok_var.get().strip():
                os.environ.pop("KAGGLE_API_TOKEN", None)
            if not k_user_var.get().strip():
                os.environ.pop("KAGGLE_USERNAME", None)
            if not k_key_var.get().strip():
                os.environ.pop("KAGGLE_KEY", None)
            notes = apply_tokens(
                kaggle_token=k_tok_var.get(),
                kaggle_username=k_user_var.get(),
                kaggle_key=k_key_var.get(),
                save_kaggle_json=save_json_var.get(),
                api_keys={k: v.get() for k, v in api_vars.items()},
            )
            if notes:
                for n in notes:
                    self._append_log(f"[OK] {n}\n", "ok")
            else:
                self._append_log("[WARN] No tokens saved.\n", "warn")
            self._sync_token_button()
            dlg.destroy()

        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=dlg.destroy).pack(side="right")
        ttk.Button(btns, text="Save Tokens", style="Accent.TButton", command=_save_and_close).pack(
            side="right", padx=(0, 8)
        )

    def _bind_toggle_tree(self, tree: Any) -> None:
        """Allow clicking the single selected row again (without Ctrl) to deselect it."""
        def _on_click(event: Any) -> str | None:
            if tree.identify_region(event.x, event.y) != "cell":
                return None
            row = tree.identify_row(event.y)
            if not row:
                return None
            # Shift (0x1) / Control (0x4): keep native multi-select behavior
            if not (event.state & 0x5) and tree.selection() == (row,):
                tree.selection_remove(row)
                return "break"
            return None

        tree.bind("<Button-1>", _on_click, add=True)

    def _select_all_new_kaggle_models(self) -> None:
        items = self.kg_new_tree.get_children()
        if items:
            self.kg_new_tree.selection_set(items)
            self._on_select_new_kaggle_model()

    def _clear_selected_kaggle_models(self) -> None:
        self._syncing_kg_sel = True
        try:
            self.kg_model_var.set("")
            for tree in (self.kg_new_tree, self.kg_tree):
                sel = tree.selection()
                if sel:
                    tree.selection_remove(sel)
            self.kg_discover_status_var.set("Model Filter cleared (all models targeted).")
        finally:
            self._syncing_kg_sel = False

    def _sync_kaggle_selection(self, active_tree: Any, other_tree: Any) -> None:
        if getattr(self, "_syncing_kg_sel", False):
            return
        self._syncing_kg_sel = True
        try:
            sel = active_tree.selection()
            if sel:
                other_sel = other_tree.selection()
                if other_sel:
                    other_tree.selection_remove(other_sel)
            names: list[str] = []
            for tree in (self.kg_new_tree, self.kg_tree):
                for item in tree.selection():
                    vals = tree.item(item, "values")
                    if vals and str(vals[0]) not in names:
                        names.append(str(vals[0]))
            if not names:
                self.kg_model_var.set("")
                self.kg_discover_status_var.set("Model Filter cleared (all models targeted).")
            elif len(names) == 1:
                self.kg_model_var.set(names[0])
                self.kg_discover_status_var.set(f"Selected '{names[0]}' in Model Filter (-m) for Pull / Run.")
            else:
                self.kg_model_var.set(", ".join(names))
                self.kg_discover_status_var.set(f"Selected {len(names)} models in Model Filter (-m) for Pull / Run.")
        finally:
            self._syncing_kg_sel = False

    def _on_select_new_kaggle_model(self, _event: Any = None) -> None:
        self._sync_kaggle_selection(self.kg_new_tree, self.kg_tree)

    def _on_select_cov_model(self, _event: Any = None) -> None:
        self._sync_kaggle_selection(self.kg_tree, self.kg_new_tree)

    def _discover_kaggle_models(self) -> None:
        """Query `kaggle b t models` in background and display new models in Step 1."""
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                self._append_log("[WARN] A command is already running. Stop it first.\n", "warn")
                return

        self.select_page("kaggle")
        self.kg_discover_status_var.set("Querying Kaggle servers (`kaggle b t models`)...")
        self.cmd_label_var.set("kaggle b t models (new vs local)")
        self._append_log("\n$ kaggle b t models  # comparing against local models\n", "cmd")
        self._set_running(True)

        known = set(_all_model_slugs())

        def worker() -> None:
            rc = 0
            try:
                proc = run_kaggle_captured(["b", "t", "models"], timeout=60)
                if proc.returncode != 0:
                    err_txt = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
                    self._log_queue.put(("line", f"[ERR] `kaggle b t models` failed: {err_txt}\n"))
                    self._log_queue.put(("discover_err", f"Failed: {err_txt[:90]}"))
                    if "401" in err_txt or "auth" in err_txt.lower() or "token" in err_txt.lower():
                        self._log_queue.put(("line", "[HINT] Click 'Set Kaggle Token' to enter KAGGLE_API_TOKEN.\n"))
                    rc = proc.returncode or 1
                else:
                    avail = parse_kaggle_models_output(proc.stdout or "")
                    new_models = [m for m in avail if canonical_model_slug(m) not in known]
                    self._log_queue.put((
                        "line",
                        f"Found {len(avail)} model(s) on Kaggle; {len(new_models)} not yet local:\n",
                    ))
                    for m in new_models:
                        self._log_queue.put(("line", f"  [NEW] {m}  -> canonical: {canonical_model_slug(m)}\n"))
                    if not new_models:
                        self._log_queue.put(("line", "[OK] All Kaggle platform models are already known locally.\n"))
                    self._log_queue.put(("discovered", (len(avail), new_models)))
            except OSError as exc:
                self._log_queue.put(("line", f"[ERR] Failed to launch kaggle: {exc}\n"))
                self._log_queue.put(("discover_err", f"Launch error: {exc}"))
                rc = 1
            except Exception as exc:
                self._log_queue.put(("line", f"[ERR] {exc}\n"))
                self._log_queue.put(("discover_err", f"Error: {exc}"))
                rc = 1
            finally:
                self._log_queue.put(("done", rc))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ Subprocess runner

    def run_cli(self, args: list[str]) -> None:
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                self._append_log("[WARN] A command is already running. Stop it first.\n", "warn")
                return

        self._last_args = list(args)
        preview = format_cmd_preview(args)
        self.cmd_label_var.set(preview)
        self._append_log(f"\n$ {preview}\n", "cmd")
        self._set_running(True)

        cmd = [sys.executable, "-m", "synhalees", "--no-color", *args]
        env = dict(
            os.environ,
            PYTHONIOENCODING="utf-8",
            PYTHONUTF8="1",
            PYTHONUNBUFFERED="1",
        )

        def worker() -> None:
            rc = 1
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                with self._proc_lock:
                    self._proc = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    self._log_queue.put(("line", line))
                rc = proc.wait()
            except OSError as exc:
                self._log_queue.put(("line", f"[ERR] Failed to launch process: {exc}\n"))
            finally:
                with self._proc_lock:
                    self._proc = None
                self._log_queue.put(("done", rc))

        threading.Thread(target=worker, daemon=True).start()

    def stop_command(self) -> None:
        with self._proc_lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            self._append_log("[WARN] Stopping active process...\n", "warn")
            try:
                proc.terminate()
            except Exception:
                proc.kill()

    def _set_running(self, running: bool) -> None:
        if running:
            self.status_badge.configure(text="RUNNING", bg=PALETTE["accent"], fg="#ffffff")
            self.stop_btn.configure(state="normal")
        else:
            self.status_badge.configure(text="IDLE", bg=PALETTE["card"], fg=PALETTE["ok"])
            self.stop_btn.configure(state="disabled")

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "line":
                    tag = None
                    if "[OK]" in payload or payload.startswith("OK:") or "[NEW]" in payload:
                        tag = "ok"
                    elif "[WARN]" in payload or "WinError 4551" in payload:
                        tag = "warn"
                    elif "[ERR]" in payload or "[FAIL]" in payload or "Traceback" in payload:
                        tag = "err"
                    self._append_log(payload, tag)
                    if "WinError 4551" in payload:
                        self._append_log(
                            "[HINT] Windows Application Control blocked kaggle.exe / Python313. "
                            "Install `kaggle` in an allowed Python (e.g. `py -3.11 -m pip install kaggle`) "
                            "and launch with `py -3.11 -m synhalees gui`, or use Colab (`kaggle/colab_slim_export.ipynb`).\n",
                            "warn",
                        )
                elif kind == "discovered":
                    total_avail, new_models = payload
                    self.kg_new_tree.delete(*self.kg_new_tree.get_children())
                    if new_models:
                        for m in new_models:
                            self.kg_new_tree.insert("", "end", values=(m, canonical_model_slug(m), "NEW on Kaggle"))
                        self.kg_new_frame.pack(fill="x", pady=(0, self._px(10)), before=self.kg_cov_card)
                        self.kg_discover_status_var.set(
                            f"Found {len(new_models)} new model(s) out of {total_avail} on Kaggle (click a row to select)."
                        )
                    else:
                        self.kg_new_frame.pack_forget()
                        self.kg_discover_status_var.set(
                            f"Checked {total_avail} Kaggle models — all are already known locally."
                        )
                elif kind == "discover_err":
                    self.kg_discover_status_var.set(str(payload))
                elif kind == "done":
                    rc = int(payload)
                    self._set_running(False)
                    if rc == 0:
                        self._append_log("[OK] Finished (exit 0).\n", "ok")
                    else:
                        self._append_log(f"[ERR] Exited with code {rc}.\n", "err")
                    self.refresh_all()
        except queue.Empty:
            pass
        self.root.after(70, self._drain_queue)

    def _append_log(self, text: str, tag: str | None = None) -> None:
        self.console.configure(state="normal")
        if tag:
            self.console.insert("end", text, tag)
        else:
            self.console.insert("end", text)
        self.console.see("end")
        self.console.configure(state="disabled")

    def _clear_log(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _copy_last_cmd(self) -> None:
        preview = format_cmd_preview(self._last_args)
        self.root.clipboard_clear()
        self.root.clipboard_append(preview)
        self._append_log(f"[OK] Copied to clipboard: {preview}\n", "muted")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def add_gui_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach `synhalees gui` flags to *parser*."""
    parser.add_argument(
        "--tab",
        choices=TAB_KEYS,
        default="overview",
        help="Initial workflow section to focus (default: overview).",
    )
    return parser


def launch_gui(initial_tab: str = "overview") -> int:
    """Create the Tk root window and run the SynhalEES Studio loop."""
    _enable_hidpi()
    import tkinter as tk

    root = tk.Tk()
    icon_png = ROOT / "docs" / "assets" / "icon.png"
    if icon_png.is_file():
        try:
            photo = tk.PhotoImage(file=str(icon_png))
            root.iconphoto(True, photo)
            root._synhalees_icon = photo  # prevent GC
        except Exception:
            pass

    SynhaleesGUI(root, initial_tab=initial_tab)
    try:
        root.state("zoomed")
    except Exception:
        try:
            root.attributes("-zoomed", True)
        except Exception:
            pass
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="synhalees gui", description="Launch the SynhalEES desktop GUI.")
    add_gui_arguments(ap)
    args = ap.parse_args(argv)
    return launch_gui(initial_tab=args.tab)


if __name__ == "__main__":
    raise SystemExit(main())
