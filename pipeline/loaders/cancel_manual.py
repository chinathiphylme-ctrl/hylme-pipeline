# -*- coding: utf-8 -*-
"""อ่านไฟล์ 'ออเดอร์ยกเลิก / ไม่ได้จัดส่ง' ที่ทีม Hylme ทำเอง

พอร์ตจาก index.html: manualCancelHeader + parseManualCancelWorkbook
หัวตารางที่ต้องมีครบ: Order ID | วันที่สั่งซื้อ | ชื่อลูกค้า | ยอดเงิน
(หัวตารางไม่จำเป็นต้องอยู่บรรทัดแรก — ค้นใน 12 บรรทัดแรกของทุกชีต)

ไฟล์นี้ระบุ 'ยอดเงินจริง' มาโดยตรง (รวม VAT) จึงใช้เป็นยอดที่ต้องหักออกแบบตรงตัว
"""
from __future__ import annotations

from pathlib import Path

from . import LoadResult, read_sheet_matrix, sheet_names
from .. import util
from ..config import Config

REQUIRED = ["Order ID", "วันที่สั่งซื้อ", "ชื่อลูกค้า", "ยอดเงิน"]


def _find_header(aoa: list[list]) -> tuple[int, list[str]] | None:
    """index.html: manualCancelHeader"""
    for i in range(min(12, len(aoa))):
        row = [util.norm(x) for x in (aoa[i] or [])]
        lowered = [x.lower() for x in row]
        if all(k.lower() in lowered for k in REQUIRED):
            return i, row
    return None


def detect(path: Path) -> str | None:
    """คืนชื่อชีตถ้าเป็นไฟล์ออเดอร์ยกเลิก (index.html: isManualCancelWorkbook)"""
    for sn in sheet_names(path):
        aoa = read_sheet_matrix(path, sn, max_rows=12)
        if _find_header(aoa):
            return sn
    return None


def load(path: Path, cfg: Config, sheet: str | None = None) -> LoadResult:
    path = Path(path)
    for sn in ([sheet] if sheet else sheet_names(path)):
        aoa = read_sheet_matrix(path, sn)
        hit = _find_header(aoa)
        if not hit:
            continue
        head_idx, head_row = hit
        idx = {name: i for i, name in enumerate(head_row) if name}

        def cell(row, name):
            i = idx.get(name)
            return row[i] if (i is not None and i < len(row)) else None

        res = LoadResult(kind="cancel", file_name=path.name, sheet=sn)
        total = 0.0
        for row in aoa[head_idx + 1:]:
            if not row:
                continue
            order_id = str(cell(row, "Order ID") or "").strip()
            if not order_id:
                continue
            gross = util.rv(util.num(cell(row, "ยอดเงิน")))
            total = util.rv(total + gross)
            res.cancels.append({
                "cancel_key": "MANUAL-CANCEL-" + order_id,
                "mc_no": "",
                "shop_no": order_id,
                "chan_no": "",
                "channel": cfg.statuses["cancel_manual_name"],
                "cancel_date": util.manual_cancel_date(cell(row, "วันที่สั่งซื้อ")),
                "cancel_gross": gross,
                "phone": util.cx_phone(cell(row, "เบอร์โทร")),
                "name": util.cx_name(cell(row, "ชื่อลูกค้า")),
                "status_raw": "ยกเลิก / ไม่ได้จัดส่ง",
                "source": "manual-cancel",
                "file_name": path.name,
            })
        dates = sorted(c["cancel_date"] for c in res.cancels if c["cancel_date"])
        res.rows_read = len(res.cancels)
        res.stats = {
            "rows_read": len(res.cancels),
            "cancels_found": len(res.cancels),
            "total_gross": total,
            "date_from": dates[0] if dates else None,
            "date_to": dates[-1] if dates else None,
        }
        return res

    raise ValueError(
        f"{path.name}: ไม่พบหัวตารางออเดอร์ยกเลิก "
        f"(ต้องมีครบทั้ง {' / '.join(REQUIRED)})"
    )
