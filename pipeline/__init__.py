# -*- coding: utf-8 -*-
"""Hylme data pipeline.

โครงสร้าง
    config.py    อ่านค่าจาก config/*.json
    util.py      ฟังก์ชันพื้นฐาน (เบอร์โทร วันที่ เงิน การปัดเศษ)
    db.py        เชื่อมต่อ SQLite + สร้าง/อัปเกรดตาราง
    loaders/     อ่านไฟล์ Excel แต่ละชนิด -> โครงสร้างกลาง
    normalize.py แปลงโครงสร้างกลาง -> แถวที่จะเขียนลงฐานข้อมูล
    reconcile.py จับคู่ออเดอร์ยกเลิกกับออเดอร์จริง
    validate.py  ตรวจความถูกต้องก่อนสร้าง output
    report.py    รายงานผลการรันให้คนอ่าน
    exports/     สร้างไฟล์ให้ MyCloud / Dashboard / Telesales
"""

__version__ = "1.0.0"
