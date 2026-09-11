# -*- coding: utf-8 -*-
"""ย้าย MKP_TRUTH และ MKP_FACTOR ออกจาก index.html เข้าฐานข้อมูล — รันครั้งเดียว

    python migrate_mkp_truth.py ..\\index.html

ทำไมต้องย้าย
    index.html บรรทัด 1358-1359 เก็บยอดจริงรายออเดอร์ของ marketplace ไว้ 1,275 รายการ
    และตัวคูณสำรองรายเดือน ไว้ในโค้ดโดยตรง
    การแก้ยอดขาย Shopee ของเดือนที่แล้วจึงต้องแก้ JavaScript

หลังรันตัวนี้
    ยอดจริงรายออเดอร์ -> ตาราง revenue_truth ในฐานข้อมูล
    ตัวคูณรายเดือน    -> config/channels.json (คีย์ mkp_factor)
    ต่อไปแก้ได้โดยไม่ต้องแตะโค้ด
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pipeline import db, util                       # noqa: E402
from pipeline.config import CONFIG_DIR, get_config  # noqa: E402


def extract(html_path: Path) -> tuple[dict, dict]:
    text = html_path.read_text(encoding="utf-8", errors="replace")
    truth, factor = {}, {}
    m = re.search(r"const\s+MKP_TRUTH\s*=\s*(\{.*?\})\s*;", text, re.S)
    if m:
        truth = json.loads(m.group(1))
    m = re.search(r"const\s+MKP_FACTOR\s*=\s*(\{.*?\})\s*;", text, re.S)
    if m:
        factor = json.loads(m.group(1))
    return truth, factor


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    html_path = Path(argv[1]) if len(argv) > 1 else (HERE.parent / "index.html")
    if not html_path.exists():
        print(f"ไม่พบไฟล์ {html_path}")
        print("ใช้:  python migrate_mkp_truth.py <path ของ index.html>")
        return 1

    truth, factor = extract(html_path)
    print(f"อ่านจาก {html_path.name}")
    print(f"  MKP_TRUTH  {len(truth):,} รายการ")
    print(f"  MKP_FACTOR {len(factor):,} รายการ")
    if not truth and not factor:
        print("ไม่พบค่าทั้งสองตัวในไฟล์นี้ — อาจย้ายไปแล้ว")
        return 0

    cfg = get_config()
    con = db.connect(cfg.db_path)
    db.ensure_schema(con)
    now = util.now_str()

    # ยอดจริงรายออเดอร์: คีย์ในไฟล์คือเลขออเดอร์ของแพลตฟอร์ม (GoSell Order ID)
    n = 0
    for external_id, gross in truth.items():
        con.execute(
            "INSERT INTO revenue_truth(system, external_id, settled_gross, report_file, loaded_at) "
            "VALUES ('marketplace', ?, ?, ?, ?) "
            "ON CONFLICT(system, external_id) DO UPDATE SET settled_gross=excluded.settled_gross, "
            "report_file=excluded.report_file, loaded_at=excluded.loaded_at",
            (str(external_id), float(gross), html_path.name, now),
        )
        n += 1
    con.commit()
    con.close()
    print(f"เขียนลงตาราง revenue_truth แล้ว {n:,} รายการ")

    if factor:
        path = CONFIG_DIR / "channels.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["mkp_factor"] = factor
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"เขียนตัวคูณรายเดือนลง {path.name} แล้ว {len(factor):,} รายการ")

    print()
    print("เสร็จแล้ว — ต่อไปแก้ยอดได้ที่ฐานข้อมูลและไฟล์ config โดยไม่ต้องแตะโค้ด")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
