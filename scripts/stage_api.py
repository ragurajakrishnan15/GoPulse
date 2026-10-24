"""Build the self-contained folder that is deployed to Vercel as the API.

    python scripts/stage_api.py            # writes deploy/api/ (git-ignored)
    cd deploy/api && vercel deploy --prod

Why stage instead of deploying the repository root: the API needs only `services/api/app` and the `pipelines` package (the demo
endpoint runs the real pipeline). Staging keeps the bundle small, avoids the repo's dev-only requirements.txt (dbt, pytest, ...),
and means the deployment does not depend on how the monorepo happens to be laid out.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "infrastructure" / "vercel" / "api"
# `pipelines/spark` needs pyspark and is never imported by the API.
PIPELINES_EXCLUDE = {"spark", "__pycache__"}


def _ignore(*names: str):
    return shutil.ignore_patterns(*names, "*.pyc")


def stage(out: Path) -> list[str]:
    if out.exists():
        shutil.rmtree(out)
    (out / "services" / "api").mkdir(parents=True)
    shutil.copytree(ROOT / "pipelines", out / "pipelines", ignore=_ignore(*PIPELINES_EXCLUDE))
    shutil.copyfile(ROOT / "services" / "__init__.py", out / "services" / "__init__.py")
    shutil.copyfile(ROOT / "services" / "api" / "__init__.py", out / "services" / "api" / "__init__.py")
    shutil.copytree(ROOT / "services" / "api" / "app", out / "services" / "api" / "app", ignore=_ignore("__pycache__"))
    for f in ("index.py", "vercel.json", ".python-version"):
        shutil.copyfile(TEMPLATES / f, out / f)
    shutil.copyfile(ROOT / "services" / "api" / "requirements.txt", out / "requirements.txt")
    return sorted(str(p.relative_to(out)).replace("\\", "/") for p in out.rglob("*") if p.is_file())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ROOT / "deploy" / "api")
    a = p.parse_args(argv)
    files = stage(a.out)
    size = sum((a.out / f).stat().st_size for f in files)
    print(f"staged {len(files)} files, {size / 1e6:.2f} MB -> {a.out}")


if __name__ == "__main__":
    main()
