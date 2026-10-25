"""Boundary rules from docs/repo-architecture.md §3, enforced by parsing imports."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "domain": set(),
    "exchange": {"domain"},
    "market": {"domain", "exchange"},
    "strategy": {"domain"},
    "risk": {"domain", "strategy"},
    "execution": {"domain", "exchange", "strategy", "risk"},
    "ledger": {"domain"},
}
IO_MODULES = {"requests", "sqlite3", "socket", "urllib", "httpx"}


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.append(n.module)
            if n.module == "packages":
                out += [f"packages.{a.name}" for a in n.names]
    return out


def py_files(*parts):
    return [p for p in (ROOT.joinpath(*parts)).rglob("*.py") if ".venv" not in p.parts]


def test_package_dependency_direction():
    bad = []
    for pkg, allowed in ALLOWED.items():
        for f in py_files("packages", pkg):
            for m in imports(f):
                if m.startswith("packages."):
                    dep = m.split(".")[1]
                    if dep != pkg and dep not in allowed:
                        bad.append(f"{f.relative_to(ROOT)} imports {m}")
                if m.startswith("apps"):
                    bad.append(f"{f.relative_to(ROOT)} imports app {m}")
    assert not bad, bad


def test_pure_packages_do_no_io():
    bad = [f"{f.relative_to(ROOT)} imports {m}" for pkg in ("strategy", "risk", "domain")
           for f in py_files("packages", pkg) for m in imports(f) if m.split(".")[0] in IO_MODULES]
    assert not bad, bad


def test_apps_never_import_private_modules():
    bad = [f"{f.relative_to(ROOT)} imports {m}" for f in py_files("apps") for m in imports(f)
           if m.startswith("packages.") and any(part.startswith("_") for part in m.split(".")[1:])]
    assert not bad, bad


def test_only_keys_py_reads_environment():
    bad = []
    for f in py_files("packages") + py_files("apps"):
        if f.name == "keys.py":
            continue
        src = f.read_text()
        if "os.environ" in src or "os.getenv" in src:
            bad.append(str(f.relative_to(ROOT)))
    assert not bad, bad
