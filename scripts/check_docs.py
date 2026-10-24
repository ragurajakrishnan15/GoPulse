"""Fail if any relative link or image in the Markdown files points at a file that does not exist.

    python scripts/check_docs.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "node_modules", ".git", "data", ".next", "target", "dbt_packages"}
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_SRC = re.compile(r"""<(?:img|a)\s[^>]*?(?:src|href)=["']([^"']+)["']""")


def md_files():
    for p in ROOT.rglob("*.md"):
        if not (SKIP_DIRS & set(p.relative_to(ROOT).parts)):
            yield p


def main() -> int:
    bad = []
    n = 0
    for f in md_files():
        text = re.sub(r"```.*?```", "", f.read_text(encoding="utf-8"), flags=re.DOTALL)  # ignore code blocks
        for target in [*LINK.findall(text), *HTML_SRC.findall(text)]:
            if re.match(r"^(https?:|mailto:|#|tel:)", target):
                continue
            path = unquote(target.split("#")[0])
            if not path:
                continue
            n += 1
            if not (f.parent / path).resolve().exists():
                bad.append(f"{f.relative_to(ROOT)}: {target}")
    for b in bad:
        print("BROKEN", b)
    print(f"checked {n} relative links in {sum(1 for _ in md_files())} files: {len(bad)} broken")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
