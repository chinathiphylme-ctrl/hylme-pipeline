# -*- coding: utf-8 -*-
"""รอบอัปเดตการจัดส่ง — รันหลัง MyCloud จัดส่งเสร็จ

    python update_delivery.py

ทำอะไรบ้าง
    1. อ่านเฉพาะไฟล์ MyCloud และไฟล์ออเดอร์ยกเลิกใน data/raw/
    2. อัปเดตสถานะการจัดส่งของออเดอร์เดิม (ไม่สร้างออเดอร์ซ้ำ)
    3. จับคู่ออเดอร์ยกเลิก
    4. ตรวจความถูกต้อง
    5. สร้างข้อมูลแดชบอร์ด + เทเลเซล + โมเดล ใหม่
    ไม่สร้างไฟล์ MyCloud (รอบเช้าสร้างไปแล้ว)

ต่างจาก daily_pipeline.py อย่างเดียวคือเลือกชนิดไฟล์และไม่ทำไฟล์ MyCloud
โค้ดจริงเป็นตัวเดียวกันทั้งหมด จึงไม่มีทางที่สองรอบจะคำนวณไม่เหมือนกัน

ตัวเลือกเพิ่มเติมใช้ได้เหมือน daily_pipeline.py เช่น
    python update_delivery.py --recheck-cancels
    python update_delivery.py --rebuild-engine
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_pipeline                                   # noqa: E402
from pipeline import report                             # noqa: E402

PRESET = ["--only", "mycloud,cancel,pagestat", "--skip-mc-export",
          "--title", "HYLME UPDATE DELIVERY"]


if __name__ == "__main__":
    try:
        sys.exit(daily_pipeline.main(PRESET + sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nยกเลิกโดยผู้ใช้")
        sys.exit(130)
    except Exception:                                   # noqa: BLE001
        import traceback
        report.use_utf8_console()
        print("\nเกิดข้อผิดพลาดที่ไม่ได้คาดไว้:\n")
        traceback.print_exc()
        print("\nคัดลอกข้อความข้างบนทั้งหมดไปถามได้เลย")
        sys.exit(2)
