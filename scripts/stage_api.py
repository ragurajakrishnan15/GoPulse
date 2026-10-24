"""Build the self-contained folder that is deployed to Vercel as the API.

    python scripts/stage_api.py            # writes deploy/api/ (git-ignored)
    cd deploy/api && vercel deploy --prod

Why stage instead of deploying the repository root: the API needs only `services/api/app` and the `pipelines` package (the demo
endpoint runs the real pipeline). Staging keeps the bundle small, avoids the repo's dev-only requirements.txt (dbt, pytest, ...),
and means the deployment does not depend on how the monorepo happens to be laid out.

Vercel detects a FastAPI project from a `pyproject.toml` that declares its dependencies, and `[tool.vercel] entrypoint` names the app
module directly. That file is GENERATED here from services/api/requirements.txt so the dependency list has a single source of truth.
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = "services.api.app.main:app"
PYTHON = "3.12"  # Vercel's Python runtimes are 3.12+; CI also tests the API on 3.12
# `pipelines/spark` needs pyspark and is never imported by the API.
PIPELINES_EXCLUDE = {"spark", "__pycache__"}
# Vercel supplies its own ASGI server.
NOT_NEEDED_ON_VERCEL = {"uvicorn"}
KEEP = {".vercel", ".env.local", ".gitignore"}  # the project link and its local files survive re-staging


def _ignore(*names: str):
    return shutil.ignore_patterns(*names, "*.pyc")


def dependencies() -> list[str]:
    deps = []
    for line in (ROOT / "services" / "api" / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line and re.split(r"[\[<>=!~ ]", line, maxsplit=1)[0].lower() not in NOT_NEEDED_ON_VERCEL:
            deps.append(line)
    return deps


def pyproject_text() -> str:
    deps = ",\n".join(f'    "{d}"' for d in dependencies())
    return (f'[project]\nname = "gopulse-api"\nversion = "1.0.0"\nrequires-python = ">={PYTHON}"\n'
            f'dependencies = [\n{deps},\n]\n\n[tool.vercel]\nentrypoint = "{ENTRYPOINT}"\n')


def stage(out: Path) -> list[str]:
    if out.exists():
        for child in out.iterdir():
            if child.name in KEEP:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    (out / "services" / "api").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "pipelines", out / "pipelines", ignore=_ignore(*PIPELINES_EXCLUDE))
    shutil.copyfile(ROOT / "services" / "__init__.py", out / "services" / "__init__.py")
    shutil.copyfile(ROOT / "services" / "api" / "__init__.py", out / "services" / "api" / "__init__.py")
    shutil.copytree(ROOT / "services" / "api" / "app", out / "services" / "api" / "app", ignore=_ignore("__pycache__"))
    (out / "pyproject.toml").write_text(pyproject_text(), encoding="utf-8", newline="\n")
    (out / ".python-version").write_text(f"{PYTHON}\n", encoding="utf-8", newline="\n")
    return sorted(str(p.relative_to(out)).replace("\\", "/") for p in out.rglob("*")
                  if p.is_file() and ".vercel" not in p.parts and p.name not in KEEP)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ROOT / "deploy" / "api")
    a = p.parse_args(argv)
    files = stage(a.out)
    size = sum((a.out / f).stat().st_size for f in files)
    print(f"staged {len(files)} files, {size / 1e6:.2f} MB -> {a.out}")


if __name__ == "__main__":
    main()
