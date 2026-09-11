# -*- coding: utf-8 -*-
"""ตรวจว่าเครื่องพร้อมรัน pipeline หรือยัง — รันตัวนี้เป็นอันดับแรก

    python check_setup.py

ไม่แตะข้อมูลใด ๆ แค่ตรวจแล้วบอกว่าอะไรขาด และต้องพิมพ์คำสั่งอะไรเพื่อแก้
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    problems: list[str] = []
    print()
    print("─" * 62)
    print("  ตรวจความพร้อมของเครื่อง")
    print("─" * 62)
    print()

    # ---- 1. Python ----
    v = sys.version_info
    if (v.major, v.minor) >= (3, 9):
        print(f"  [ OK ]  Python {v.major}.{v.minor}.{v.micro}")
    else:
        print(f"  [XXXX]  Python {v.major}.{v.minor} เก่าเกินไป — ต้อง 3.9 ขึ้นไป")
        problems.append("ติดตั้ง Python รุ่นใหม่จาก https://www.python.org/downloads/")

    # ---- 2. openpyxl ----
    try:
        import openpyxl
        print(f"  [ OK ]  openpyxl {openpyxl.__version__}  (ใช้อ่าน/เขียนไฟล์ Excel)")
    except ImportError:
        print("  [XXXX]  ยังไม่ได้ติดตั้ง openpyxl")
        problems.append("พิมพ์คำสั่งนี้:  pip install openpyxl")

    # ---- 3. โฟลเดอร์ ----
    for name in ("config", "pipeline", "data"):
        p = HERE / name
        if p.exists():
            print(f"  [ OK ]  มีโฟลเดอร์ {name}/")
        else:
            print(f"  [XXXX]  ไม่พบโฟลเดอร์ {name}/")
            problems.append(f"โฟลเดอร์ {name}/ หายไป — คัดลอก hylme_pipeline มาใหม่ทั้งโฟลเดอร์")

    # ---- 4. ไฟล์ตั้งค่า ----
    try:
        from pipeline.config import get_config
        cfg = get_config()
        print("  [ OK ]  อ่านไฟล์ config/*.json ได้ครบ 5 ไฟล์")
    except Exception as exc:                            # noqa: BLE001
        print(f"  [XXXX]  อ่านไฟล์ตั้งค่าไม่ได้: {exc}")
        problems.append("ตรวจว่าไฟล์ใน config/ ครบและไม่ถูกแก้จนผิดรูปแบบ JSON")
        cfg = None

    # ---- 5. ฐานข้อมูล ----
    if cfg:
        dbp = cfg.db_path
        if dbp.exists():
            size = dbp.stat().st_size / (1024 * 1024)
            print(f"  [ OK ]  พบฐานข้อมูล {dbp}  ({size:,.1f} MB)")
        else:
            print(f"  [ !  ]  ยังไม่มีฐานข้อมูลที่ {dbp}")
            print("           ระบบจะสร้างให้เองตอนรันครั้งแรก (ไม่ใช่ปัญหา)")

    # ---- 6. template MyCloud ----
    tpl = HERE / "config" / "templates" / "mycloud_template.xlsx"
    if tpl.exists():
        print(f"  [ OK ]  พบ template ไฟล์ MyCloud  ({tpl.stat().st_size / 1024:,.0f} KB)")
    else:
        print("  [ !  ]  ไม่พบ config/templates/mycloud_template.xlsx")
        problems.append("ไม่มี template — ขั้นสร้างไฟล์ MyCloud จะข้ามไป ส่วนอื่นยังทำงานปกติ")

    # ---- 7. ไฟล์รอโหลด ----
    raw = HERE / "data" / "raw"
    if raw.exists():
        files = [p for p in raw.iterdir()
                 if p.is_file() and p.suffix.lower() in (".xlsx", ".xls", ".xlsm")
                 and not p.name.startswith("~$")]
        if files:
            print(f"  [ OK ]  มีไฟล์รอโหลดใน data/raw/  {len(files)} ไฟล์")
            for f in files[:10]:
                print(f"           - {f.name}")
        else:
            print("  [ !  ]  ยังไม่มีไฟล์ใน data/raw/")
            print(f"           วางไฟล์ .xlsx จาก Pancake / MyCloud ไว้ที่: {raw}")

    print()
    print("─" * 62)
    if problems:
        print("  ต้องแก้ก่อน:")
        for i, p in enumerate(problems, 1):
            print(f"    {i}. {p}")
    else:
        print("  พร้อมรันแล้ว — พิมพ์:  python daily_pipeline.py")
    print("─" * 62)
    print()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
