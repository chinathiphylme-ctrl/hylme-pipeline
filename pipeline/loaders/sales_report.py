# -*- coding: utf-8 -*-
"""อ่านไฟล์ "รายงานขายฮีลมี 2569" — เป้ายอดขายรายวัน × ช่องทาง

ไฟล์นี้คือแหล่งที่มาของ `DAY_TARGET` และ `DAY_CH_TARGET` ที่เคยฝังอยู่ใน index.html
ตอนนี้กลายเป็น input ปกติของ pipeline วางใน data/raw/ ได้เหมือนไฟล์อื่น

--------------------------------------------------------------------------
โครงไฟล์ (ตรวจจากไฟล์จริง 10 ก.ย. 2026)
--------------------------------------------------------------------------
1 ชีต = 1 เดือน (มกราคม ... มิถุนายน) · แถว 1-2 เป็นหัวตาราง 2 ชั้น · แถว 3 ขึ้นไปเป็นข้อมูลรายวัน

    A  วันที่สั่งซื้อ            I  ส่งคืนสำเร็จ (ยอดขาย)
    B  คำสั่งซื้อทั้งหมด          J  ยอดขายรวม
    C-D รอดำเนินการ             K  Facebook (คีย์)   <- ไม่ใช้
    E-F กำลังดำเนินการ           L-T ช่องทาง 9 ช่อง
    G-H สำเร็จ                  U  Upsell            V  ยอดรวม

--------------------------------------------------------------------------
สองกฎที่พิสูจน์กับไฟล์จริงแล้ว — ห้ามเปลี่ยน
--------------------------------------------------------------------------
1) เป้ารวมของวัน = **อ่านคอลัมน์ V ตรง ๆ** ห้ามคำนวณเอง

   คอมเมนต์ใน index.html เขียนว่า V = L+M+N+O+P+Q+R+S+T+U-I
   แต่พอลองคำนวณจริงพบว่า **ไม่ตรง 15 วันจาก 181 วัน** เพราะสูตรในชีตไม่เหมือนกันทุกแถว
   (15 วันนั้นไม่ได้บวก Upsell เข้าไป เช่น 24 มี.ค. · 27 มี.ค. · 20 เม.ย.)
   การอ่านค่าที่เก็บไว้ในเซลล์ตรง ๆ ตรงกับ DAY_TARGET เดิม **181 / 181 วัน**

2) เป้ารายช่องทาง = ค่าในคอลัมน์นั้น x (V / ผลรวม L:T)

   คือเกลี่ย Upsell และการหักของส่งคืนเข้าไปตามสัดส่วน โดย **Upsell ไม่อยู่ในตัวหาร**
   ช่องทางที่ยอดเป็น 0 ไม่ต้องใส่
   ตรวจแล้วตรงกับ DAY_CH_TARGET เดิม **946 / 946 ช่อง** (คลาดสูงสุด 0.05 บาท จากการปัดเศษ)

แถวรวมท้ายชีตถูกข้าม — นับเฉพาะแถวที่คอลัมน์ A เป็นวันที่จริง
(index.html ก็ทำแบบนี้ เพราะชีตพฤษภาคมสูตรรวมตกวันที่ 31 ไป)
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from . import LoadResult, read_sheet_matrix, sheet_names
from .. import util
from ..config import Config

TOTAL_KEY = "__TOTAL__"
HEADER_A1 = "วันที่สั่งซื้อ"
HEADER_V1 = "ยอดรวม"


def _is_report_sheet(aoa: list[list]) -> bool:
    if not aoa:
        return False
    row1 = [util.norm(x) for x in (aoa[0] or [])]
    if not row1 or row1[0] != HEADER_A1:
        return False
    row2 = [util.norm(x) for x in (aoa[1] or [])] if len(aoa) > 1 else []
    return "Shopee" in row2 and any(c.startswith("Facebook") for c in row2)


def detect(path: Path) -> str | None:
    """คืนชื่อชีตแรกที่เป็นรายงานขาย หรือ None"""
    for sn in sheet_names(path):
        if _is_report_sheet(read_sheet_matrix(path, sn, max_rows=3)):
            return sn
    return None


def load(path: Path, cfg: Config, sheet: str | None = None) -> LoadResult:
    path = Path(path)
    rr = cfg.revenue
    ch_cols = {int(k): v for k, v in rr["sales_report_channel_columns"].items()}
    c_return = int(rr["sales_report_col_return"])
    c_upsell = int(rr["sales_report_col_upsell"])
    c_total = int(rr["sales_report_col_total"])

    rows: list[dict] = []
    months: list[str] = []
    n_days = 0
    upsell_total = 0.0
    return_total = 0.0
    formula_mismatch = 0        # วันที่สูตร L:U-I ไม่เท่ากับค่าในคอลัมน์ V

    for sn in sheet_names(path):
        aoa = read_sheet_matrix(path, sn)
        if not _is_report_sheet(aoa):
            continue
        sheet_days = 0
        for r in aoa[2:]:
            if not r:
                continue
            d = r[0] if len(r) > 0 else None
            if not isinstance(d, (_dt.datetime, _dt.date)):
                continue          # ข้ามแถวรวมท้ายชีตและแถวว่าง
            date = f"{d.year}-{str(d.month).zfill(2)}-{str(d.day).zfill(2)}"

            def cell(i: int) -> float:
                return util.num(r[i - 1]) if len(r) >= i else 0.0

            raw = {name: cell(i) for i, name in ch_cols.items()}
            base = sum(raw.values())
            total = util.rv(cell(c_total))          # <-- อ่านตรง ๆ ไม่คำนวณ
            upsell = cell(c_upsell)
            returned = cell(c_return)
            if abs(util.rv(base + upsell - returned) - total) > 0.02:
                formula_mismatch += 1

            rows.append({"report_date": date, "channel": TOTAL_KEY,
                         "target_inc_vat": total, "source_sheet": sn})
            factor = (total / base) if base else 1.0
            for name, v in raw.items():
                if not v:
                    continue        # ช่องทางที่ยอดเป็น 0 ไม่ต้องใส่
                rows.append({"report_date": date, "channel": name,
                             "target_inc_vat": util.rv(v * factor), "source_sheet": sn})

            n_days += 1
            sheet_days += 1
            upsell_total += upsell
            return_total += returned
        if sheet_days:
            months.append(f"{sn.strip()} ({sheet_days} วัน)")

    if not rows:
        raise ValueError(
            f"{path.name}: ไม่พบชีตที่เป็นรายงานขาย "
            f"(แถวแรกคอลัมน์ A ต้องเป็น '{HEADER_A1}' และแถวที่ 2 ต้องมีชื่อช่องทาง)"
        )

    dates = sorted({x["report_date"] for x in rows})
    res = LoadResult(kind="salesreport", file_name=path.name, sheet=sheet or "")
    res.rows_read = len(rows)
    res.sales_report = rows
    res.stats = {
        "days": n_days,
        "rows": len(rows),
        "months": months,
        "from": dates[0],
        "to": dates[-1],
        "total_target": util.rv(sum(x["target_inc_vat"] for x in rows if x["channel"] == TOTAL_KEY)),
        "upsell_total": util.rv(upsell_total),
        "return_total": util.rv(return_total),
        "formula_mismatch": formula_mismatch,
    }
    return res
