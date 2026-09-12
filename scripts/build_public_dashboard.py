#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the only files allowed in the public GitHub Pages artifact.

The source database, raw exports, and all ``data/`` files remain private. This
script copies only dashboard.html and the explicitly allow-listed aggregated
JSON files into ``public/``. It blocks the build when either PII scan layer
finds customer data.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pii_scan  # noqa: E402
from pipeline.config import get_config  # noqa: E402


# Keep this list identical to dashboard.html's FILE_LIST. Do not copy a
# directory wholesale: a new JSON file must never become public by accident.
REQUIRED_JSON = (
    "summary", "daily", "daily_team", "owners", "items", "promotions",
    "sets", "monthly", "delivery", "reconciliation", "revenue_audit",
    "conversion", "daily_channel", "payments", "products",
)

SOURCE_DASHBOARD = ROOT / "dashboard.html"
SOURCE_DATA = ROOT / "data" / "output" / "dashboard"
PUBLIC_DIR = ROOT / "public"
STAGING_DIR = ROOT / ".public-dashboard-staging"
MANAGED_MARKER = ".hylme-public-dashboard"


class PublishBlocked(RuntimeError):
    """The public artifact must not be generated."""


def source_files() -> list[Path]:
    files = [SOURCE_DASHBOARD]
    missing = [name for name in REQUIRED_JSON if not (SOURCE_DATA / f"{name}.json").is_file()]
    if missing:
        raise PublishBlocked("Missing required dashboard JSON: " + ", ".join(missing))
    files.extend(SOURCE_DATA / f"{name}.json" for name in REQUIRED_JSON)
    return files


def validate_json(files: list[Path]) -> None:
    """Reject malformed JSON and malformed compact-table payloads."""
    for path in files:
        if path.suffix != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PublishBlocked(f"Invalid JSON: {path.relative_to(ROOT)} ({exc})") from exc
        if isinstance(payload, dict) and payload.get("format") == "table":
            cols, rows = payload.get("cols"), payload.get("rows")
            if not isinstance(cols, list) or not isinstance(rows, list):
                raise PublishBlocked(f"Invalid table payload: {path.relative_to(ROOT)}")
            if any(not isinstance(row, list) or len(row) != len(cols) for row in rows):
                raise PublishBlocked(f"Invalid table row width: {path.relative_to(ROOT)}")


def run_pii_gate(files: list[Path]) -> None:
    """Run both PII scanner layers; an unavailable database blocks publishing."""
    pattern_hits = pii_scan.scan_patterns(files)
    if pattern_hits:
        names = ", ".join(sorted({f"{f} ({label})" for f, label, _ in pattern_hits}))
        raise PublishBlocked("PII pattern scan failed: " + names)

    try:
        cfg = get_config()
        db_path = cfg.db_path
        if not db_path.is_file():
            raise FileNotFoundError(db_path)
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            real_hits, staff_notes, counts = pii_scan.scan_real_values(files, con)
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        raise PublishBlocked(f"Cannot complete database-backed PII scan: {exc}") from exc

    if real_hits:
        names = ", ".join(f"{file_name} ({label}: {count})"
                          for file_name, label, count, _ in real_hits)
        raise PublishBlocked("Database-backed PII scan failed: " + names)

    print("PII gate passed: both scanner layers found no customer data.")
    # Keep console output ASCII-compatible for Windows terminals configured as cp874.
    print("Database values checked: " + " | ".join(f"{key} {value:,}" for key, value in counts.items()))
    if staff_notes:
        print("Note: employee names collide with customer names; no customer row was published.")


def write_landing_page(destination: Path) -> None:
    """Give the root Pages URL a predictable entry point."""
    destination.write_text(
        "<!doctype html>\n"
        "<html lang=\"th\"><head><meta charset=\"utf-8\">"
        "<meta http-equiv=\"refresh\" content=\"0; url=dashboard.html\">"
        "<title>Hylme Dashboard</title></head>"
        "<body><a href=\"dashboard.html\">เปิด Hylme Dashboard</a></body></html>\n",
        encoding="utf-8",
    )


def replace_public_directory() -> None:
    files = source_files()
    validate_json(files)
    run_pii_gate(files)

    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    staging_data = STAGING_DIR / "data" / "output" / "dashboard"
    staging_data.mkdir(parents=True)
    try:
        shutil.copy2(SOURCE_DASHBOARD, STAGING_DIR / "dashboard.html")
        write_landing_page(STAGING_DIR / "index.html")
        for name in REQUIRED_JSON:
            shutil.copy2(SOURCE_DATA / f"{name}.json", staging_data / f"{name}.json")
        (STAGING_DIR / MANAGED_MARKER).write_text(
            "Generated by scripts/build_public_dashboard.py. Do not place private files here.\n",
            encoding="utf-8",
        )

        if PUBLIC_DIR.exists():
            if not (PUBLIC_DIR / MANAGED_MARKER).is_file():
                raise PublishBlocked("Refusing to replace unmanaged public/ directory.")
            shutil.rmtree(PUBLIC_DIR)
        STAGING_DIR.replace(PUBLIC_DIR)
    except Exception:
        if STAGING_DIR.exists():
            shutil.rmtree(STAGING_DIR)
        raise

    print(f"Public dashboard built at {PUBLIC_DIR.relative_to(ROOT)}")
    print(f"Published files: dashboard.html, index.html, and {len(REQUIRED_JSON)} allow-listed JSON files")


def main() -> int:
    try:
        replace_public_directory()
    except PublishBlocked as exc:
        print(f"PUBLIC BUILD BLOCKED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
