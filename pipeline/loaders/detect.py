# -*- coding: utf-8 -*-
"""ดูว่าไฟล์ที่วางไว้ใน data/raw/ เป็นไฟล์ชนิดไหน

ลำดับการตรวจสำคัญ — เรียงตามความเฉพาะเจาะจงจากมากไปน้อย
เหมือนกับลำดับ if ใน handleFiles() ของ index.html
"""
from __future__ import annotations

from pathlib import Path

from ..config import Config
from . import (cancel_manual, gosell, leads_export, mycloud, oldsheet, pagestat,
               pancake, sales_report)

# (ชื่อชนิด, โมดูล) — ตรวจตามลำดับนี้
# ลำดับเดียวกับ handleFiles() ใน index.html: ไฟล์ยกเลิก -> สถิติหน้า -> MyCloud -> Pancake
# รายงานขาย Excel ตรวจก่อนสุด เพราะหัวตารางเฉพาะตัวมาก ไม่ชนกับไฟล์ชนิดอื่น
# ไฟล์เก่า 2 ชนิด ตรวจถัดมา ลายเซ็นชัดและไม่ชนกับไฟล์ปัจจุบัน
#   Google Sheet เก่า  ชื่อชีตเป็นเลข 1–31 (อย่างน้อย 3 ชีต) + คอลัมน์เฉพาะของไฟล์เก่า
#   GoSell            หัวตารางภาษาไทยเฉพาะตัว 5 จาก 7 คอลัมน์ ใน 12 แถวแรก
# gosell อยู่ท้ายสุดเพราะเป็นตัวเดียวที่ต้องเปิดไฟล์เพื่อไล่หาหัวตาราง (แพงที่สุด)
# ตัวอื่นคัดจากชื่อชีตได้ก่อน จึงเร็วมาก
DETECTORS = [
    ("salesreport", sales_report),
    ("leadsexport", leads_export),
    ("oldsheet", oldsheet),
    ("cancel", cancel_manual),
    ("pagestat", pagestat),
    ("mycloud", mycloud),
    ("pancake", pancake),
    ("gosell", gosell),
]

SUPPORTED_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


def identify(path: Path) -> tuple[str, str] | None:
    """คืน (kind, sheet_name) หรือ None ถ้าไม่รู้จัก"""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return None
    if path.name.startswith("~$"):          # ไฟล์ชั่วคราวที่ Excel สร้างตอนเปิดไฟล์ค้างไว้
        return None
    for kind, module in DETECTORS:
        try:
            sheet = module.detect(path)
        except Exception:
            continue
        if sheet:
            return kind, sheet
    return None


def load(path: Path, cfg: Config):
    """อ่านไฟล์ด้วย loader ที่ถูกต้อง"""
    found = identify(path)
    if not found:
        raise ValueError(
            f"{Path(path).name}: ไม่รู้จักรูปแบบไฟล์นี้\n"
            f"  รองรับ: Pancake export · MyCloud outbound-orders · สถิติหน้า Pancake ·\n"
            f"          ไฟล์ออเดอร์ยกเลิกของ Hylme · รายงานขาย Excel ·\n"
            f"          GoSell export · Google Sheet เก่า (ชีตชื่อ 1–31)"
        )
    kind, sheet = found
    module = dict(DETECTORS)[kind]
    return module.load(path, cfg, sheet=sheet)


def scan_inbox(raw_dir: Path) -> list[Path]:
    """คืนรายชื่อไฟล์ใน data/raw/ เรียงตามชื่อ เพื่อให้ผลการรันคงที่ทุกครั้ง"""
    raw_dir = Path(raw_dir)
    if not raw_dir.exists():
        return []
    return sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and not p.name.startswith("~$")
    )
