"""Remote Google Colab pull orchestrator using `google-colab-cli`.

Bundles ONLY the live local `synhalees/` package, `kaggle/tasks/`, and existing
`kaggle/results/*.slim.csv` (never requiring `git clone` or pushing uncommitted
changes first), runs `synhalees kaggle pull` inside the remote Colab VM, and
downloads back only the KB-sized `*.slim.csv` archive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Iterable

from synhalees.kaggle_ops import KG_RESULTS, ROOT, TASKS_DIR

_BUNDLE_SUBDIRS = ("synhalees", "kaggle/tasks")


def colab_bin() -> str:
    """Locate the `colab` CLI executable (`google-colab-cli`) or raise SystemExit."""
    found = shutil.which("colab") or shutil.which("colab.exe")
    if found:
        return found
    for candidate in (
        Path(sys.executable).parent / "Scripts" / "colab.exe",
        Path(sys.executable).parent / "colab",
        Path.home() / "AppData/Local/Programs/Python/Python313/Scripts/colab.exe",
    ):
        if candidate.exists():
            return str(candidate)
    raise SystemExit(
        "google-colab-cli not found on PATH "
        "(install on Python 3.12+: py -m pip install google-colab-cli)"
    )


_COLAB_PY_RUNNER = (
    "import importlib.metadata, sys; "
    "eps = importlib.metadata.entry_points(group='console_scripts', name='colab'); "
    "ep = next(iter(eps)); "
    "sys.exit(ep.load()())"
)


def _sys_python_has_colab() -> bool:
    """Return True if the active interpreter exposes the `colab` console script."""
    import importlib.metadata

    try:
        return bool(list(importlib.metadata.entry_points(group="console_scripts", name="colab")))
    except Exception:
        return False


def _colab_python_candidates(exe: str, rest: list[str]) -> list[list[str]]:
    """Candidate `python/py -c ...` commands when `colab.exe` is blocked (WinError 4551)."""
    p = Path(exe)
    cmds: list[list[str]] = []
    seen: set[str] = set()

    def _add(launcher: str) -> None:
        if launcher and launcher not in seen:
            seen.add(launcher)
            cmds.append([launcher, "-c", _COLAB_PY_RUNNER, *rest])

    if _sys_python_has_colab():
        _add(sys.executable)
    for cand in (p.parent.parent / "python.exe", p.parent / "python.exe"):
        if cand.is_file():
            _add(str(cand))
    py_launcher = shutil.which("py")
    if py_launcher:
        _add(py_launcher)
    return cmds


def _colab_sh(argv: Iterable[object], *, echo: bool = True) -> int:
    """Run a `colab` CLI command, bypassing blocked pip .exe shims on Windows."""
    cmd = [str(a) for a in argv]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    if os.name == "nt" and cmd and "colab" in Path(cmd[0]).name.lower() and _sys_python_has_colab():
        py_cmd = [sys.executable, "-c", _COLAB_PY_RUNNER, *cmd[1:]]
        if echo:
            print("+ " + " ".join([sys.executable, "(colab)", *cmd[1:]]), flush=True)
        return subprocess.run(py_cmd, cwd=str(ROOT), env=env).returncode

    if echo:
        print("+ " + " ".join(cmd), flush=True)
    try:
        return subprocess.run(cmd, cwd=str(ROOT), env=env).returncode
    except OSError as exc:
        if getattr(exc, "winerror", None) == 4551 and cmd and "colab" in Path(cmd[0]).name.lower():
            last_exc: OSError = exc
            for fb in _colab_python_candidates(cmd[0], cmd[1:]):
                try:
                    if echo:
                        print("+ " + " ".join([fb[0], "(colab)", *cmd[1:]]), flush=True)
                    return subprocess.run(fb, cwd=str(ROOT), env=env).returncode
                except OSError as fb_exc:
                    last_exc = fb_exc
            raise last_exc
        raise


def build_colab_bundle(out_tar: Path) -> tuple[Path, int]:
    """Pack live local `synhalees/`, `kaggle/tasks/`, and `*.slim.csv` into *out_tar*.

    Returns `(out_tar, file_count)`. Skips `__pycache__/`, `*.pyc`, and `.temp/`.
    """
    out_tar.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(out_tar, "w:gz") as tf:
        for rel_dir in _BUNDLE_SUBDIRS:
            src_dir = ROOT / rel_dir
            if not src_dir.is_dir():
                continue
            for p in sorted(src_dir.rglob("*")):
                if not p.is_file():
                    continue
                if "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo"):
                    continue
                arcname = p.relative_to(ROOT).as_posix()
                tf.add(p, arcname=arcname)
                count += 1

        if KG_RESULTS.is_dir():
            for slim in sorted(KG_RESULTS.glob("*.slim.csv")):
                if slim.is_file():
                    tf.add(slim, arcname=slim.relative_to(ROOT).as_posix())
                    count += 1

    return out_tar, count


def build_colab_runner_script(
    pull_args: list[str],
    *,
    kaggle_env: dict[str, str] | None = None,
    has_kaggle_json: bool = False,
) -> str:
    """Generate the self-contained Python script executed on the Colab runtime."""
    env_setup = []
    for key in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY"):
        val = (kaggle_env or {}).get(key, "")
        if val:
            env_setup.append(f"os.environ[{key!r}] = {val!r}")
    env_block = "\n".join(env_setup) if env_setup else "pass"

    return f'''"""Auto-generated by synhalees — runs inside Google Colab."""
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

WORK = Path("/content/synhalees_work")
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True, exist_ok=True)

with tarfile.open("/content/synhalees_bundle.tar.gz", "r:gz") as tf:
    try:
        tf.extractall(WORK, filter="data")
    except TypeError:
        tf.extractall(WORK)

{env_block}

if {bool(has_kaggle_json)!r}:
    kdir = Path.home() / ".kaggle"
    kdir.mkdir(parents=True, exist_ok=True)
    src_json = Path("/content/kaggle.json")
    if src_json.is_file():
        dst_json = kdir / "kaggle.json"
        shutil.copy2(src_json, dst_json)
        try:
            dst_json.chmod(0o600)
        except Exception:
            pass

pip_res = subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q", "-U", "kaggle"],
    capture_output=True, text=True,
)
if pip_res.returncode != 0:
    print(pip_res.stdout or "")
    print(pip_res.stderr or "")
    raise RuntimeError(f"pip install kaggle failed (exit {{pip_res.returncode}})")

import threading

cmd = [sys.executable, "-m", "synhalees", "--no-color", *{pull_args!r}]
print("+ " + " ".join(cmd), flush=True)
env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
proc = subprocess.Popen(
    cmd,
    cwd=str(WORK),
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
)
stop_hb = threading.Event()
last_out = [0.0]

def _reader() -> None:
    import time
    assert proc.stdout is not None
    for line in proc.stdout:
        last_out[0] = time.monotonic()
        print(line, end="", flush=True)

def _heartbeat() -> None:
    import time
    while not stop_hb.wait(15):
        if time.monotonic() - last_out[0] >= 14:
            print("[colab] still downloading/processing...", flush=True)

rt = threading.Thread(target=_reader, daemon=True)
hb = threading.Thread(target=_heartbeat, daemon=True)
rt.start()
hb.start()
rc = proc.wait()
stop_hb.set()
rt.join(timeout=5)

out_tar = Path("/content/synhalees_slim_out.tar.gz")
with tarfile.open(out_tar, "w:gz") as tf:
    res_dir = WORK / "kaggle" / "results"
    if res_dir.is_dir():
        for slim in sorted(res_dir.glob("*.slim.csv")):
            tf.add(slim, arcname=slim.name)

print(f"[OK] Packed slim archive: {{out_tar}} ({{out_tar.stat().st_size}} bytes)", flush=True)
if rc != 0:
    raise RuntimeError(f"synhalees kaggle pull failed inside Colab (exit {{rc}})")
'''


def extract_slim_archive(archive: Path, dest_dir: Path = KG_RESULTS) -> list[Path]:
    """Extract only `*.slim.csv` files safely from *archive* into *dest_dir*."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name
            if not name.endswith(".slim.csv"):
                continue
            src = tf.extractfile(member)
            if src is None:
                continue
            target = dest_dir / name
            target.write_bytes(src.read())
            extracted.append(target)
    return extracted


def pull_via_colab(
    *,
    task: str = "all",
    modality: str | None = None,
    force: bool = False,
    models: list[str] | None = None,
    extra: list[str] | None = None,
    dry_run: bool = False,
    keep_session: bool = False,
) -> int:
    """Bundle local SynhalEES code, execute `kaggle pull` on Colab, and fetch `*.slim.csv`."""
    pull_args = ["kaggle", "pull", task]
    if modality:
        pull_args.extend(["--modality", modality])
    if force:
        pull_args.append("--force")
    for m in models or ():
        pull_args.extend(["-m", m])
    if extra:
        pull_args.extend(extra)

    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    try:
        has_json = kaggle_json.is_file()
    except OSError:
        has_json = False

    kaggle_env = {
        k: os.environ[k]
        for k in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY")
        if os.environ.get(k)
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bundle_path, n_files = build_colab_bundle(tmp_path / "synhalees_bundle.tar.gz")
        size_kb = max(1, round(bundle_path.stat().st_size / 1024))
        runner_path = tmp_path / "run_pull_colab.py"
        runner_path.write_text(
            build_colab_runner_script(
                pull_args,
                kaggle_env=kaggle_env,
                has_kaggle_json=has_json,
            ),
            encoding="utf-8",
        )

        if dry_run:
            print(f"[dry-run] bundle: {n_files} files ({size_kb} KB) from local workspace (no git clone)")
            print(f"[dry-run] remote cmd: synhalees {' '.join(pull_args[1:])}")
            print("[dry-run] colab flow: colab new -> upload bundle -> exec runner -> download slim tar -> stop")
            return 0

        cbin = colab_bin()
        print(f"Bundled {n_files} local files ({size_kb} KB) -- no git clone needed.")
        if not kaggle_env and not has_json:
            raise SystemExit(
                "No Kaggle credentials found (set KAGGLE_API_TOKEN or ~/.kaggle/kaggle.json first)"
            )

        rc = _colab_sh([cbin, "new"])
        if rc != 0:
            return rc

        try:
            rc = _colab_sh([cbin, "upload", str(bundle_path), "/content/synhalees_bundle.tar.gz"])
            if rc != 0:
                return rc

            if has_json:
                rc = _colab_sh([cbin, "upload", str(kaggle_json), "/content/kaggle.json"])
                if rc != 0:
                    return rc

            exec_rc = _colab_sh([cbin, "exec", "--timeout", "600", "-f", str(runner_path)])

            out_archive = tmp_path / "synhalees_slim_out.tar.gz"
            dl_rc = _colab_sh([cbin, "download", "/content/synhalees_slim_out.tar.gz", str(out_archive)])
            if dl_rc == 0 and out_archive.is_file():
                extracted = extract_slim_archive(out_archive, KG_RESULTS)
                print(f"[OK] Synced {len(extracted)} slim CSV(s) from Colab into {KG_RESULTS}")
                if exec_rc == 0:
                    print("[next] Run `synhalees kaggle submit all --build` to update submissions/ & site data.")
            if exec_rc != 0:
                return exec_rc
            if dl_rc != 0:
                return dl_rc
            return 0
        finally:
            if not keep_session:
                _colab_sh([cbin, "stop"])
