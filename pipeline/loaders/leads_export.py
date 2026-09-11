# -*- coding: utf-8 -*-
"""อ่านไฟล์ที่ export ออกมาจากแดชบอร์ดเดิม (ชีต Leads / Leads-Products)

--------------------------------------------------------------------------
ไฟล์นี้ไม่ใช่ export ดิบจาก GoSell — มันคือผลลัพธ์ของ index.html เอง
--------------------------------------------------------------------------
ไฟล์ชื่อ "gosell Orders ..." ที่ทีมมี เปิดดูแล้วเป็นชีต `Leads` / `Leads-Products`
ซึ่งเป็นรูปแบบ ReadyPlanet ที่ `convertGoSellData()` แปลงออกมา แล้ว `bakeAndDownload()`
เขียนลงไฟล์ พร้อมคอลัมน์ภายในของแดชบอร์ดที่ขึ้นต้นด้วย `__`

โชคดีที่คอลัมน์ `__` เหล่านั้นเก็บ "ยอดก่อนเกลี่ย" ไว้ให้ครบ (index.html บรรทัด 6551-6552)

    if(r.__origValue === undefined) r.__origValue = Number(r['Lead Value']) || 0;
    r['Lead Value'] = r.__origValue;          // รีเซ็ตกลับยอดดิบก่อนคำนวณใหม่
    ...
    r['Lead Value'] = r.__origValue * ratio;  // แล้วค่อยเกลี่ย

แปลว่าไฟล์นี้มีครบทั้งสองชั้น

    ยอดดิบ      = `__origValue`  ถ้าไม่มีค่า -> `Lead Value` (แปลว่าใบนั้นไม่เคยถูกเกลี่ย)
    ยอดที่แสดง   = `Lead Value`  (ยอดที่แดชบอร์ดเดิมโชว์)

พิสูจน์แล้วด้วยไฟล์จริง 2 ไฟล์ที่ช่วง เม.ย. ทับกัน 1,388 ใบ
ไฟล์เก่ามี `__origValue` ไฟล์ใหม่ไม่มี (ยังไม่ถูกเกลี่ย) ยอดดิบตรงกัน **1,387 จาก 1,388 ใบ**
(อีก 1 ใบต่างกัน 28 บาท เพราะมีการแก้ที่ GoSell ระหว่างการ export สองครั้ง)

`Lead Value` ของไฟล์ถูกเก็บไว้ใน `source_audit.mapping.dashboard_reported_ex_vat`
เพื่อใช้ตรวจว่าเครื่องเกลี่ยของเราคำนวณออกมาได้ตรงกับที่แดชบอร์ดเดิมโชว์หรือไม่
**ไม่ได้เอาไปเขียนลง `orders`** — ยอดที่แสดงต้องมาจากชั้นกระทบยอดของเราเองเท่านั้น

--------------------------------------------------------------------------
ข้อควรรู้เรื่องข้อมูลส่วนบุคคล
--------------------------------------------------------------------------
ไฟล์ที่ export ตอนเปิดโหมดปิดบังจะได้ชื่อ/ที่อยู่แบบ `น******ง` และเบอร์เป็นค่าว่าง
loader ตัดค่าที่ถูกปิดบังทิ้ง (ไม่เก็บดาวลงฐาน) และนับไว้ในสถิติ
ผลคือใบเหล่านั้นจับคู่ลูกค้าซ้ำไม่ได้ — เป็นข้อจำกัดของไฟล์ ไม่ใช่ของ pipeline
"""
from __future__ import annotations

import re
from pathlib import Path

from . import LoadResult, open_workbook, sheet_names_fast
from .. import normalize as nz
from .. import util
from ..config import Config

SYSTEM = "leads_export"
SHEET_LEADS = "Leads"
SHEET_PRODUCTS = "Leads-Products"

LEAD_KEYS = ["Lead ID", "Lead Value", "Lead Purchase Date"]


def _num(v):
    """คืน None เมื่อ "ไม่มีค่า" (ต่างจาก 0 ที่เป็นค่าจริง) — สำคัญกับ __origValue"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() == "none" or s == "-":
        return None
    try:
        return float(re.sub(r"[฿$,]", "", s))
    except ValueError:
        return None


def _txt(v) -> str:
    """ข้อความที่ใช้ได้จริง — ค่าที่ถูกปิดบัง (`น******ง`) และ 'None' ถือว่าว่าง"""
    s = util.norm(v)
    if not s or s.lower() == "none" or s == "-" or util.is_masked(s):
        return ""
    return s


def _rows_of(ws) -> list[dict]:
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if header is None:
        return []
    cols = [(str(h).strip() if h is not None else "") for h in header]
    out = []
    for i, r in enumerate(it):
        if r is None or all(v is None for v in r):
            continue
        d = {cols[j]: r[j] for j in range(min(len(cols), len(r))) if cols[j]}
        d["__row"] = i + 2                    # เลขแถวตามที่เห็นใน Excel
        out.append(d)
    return out


def _ts(v) -> str:
    s = util.norm(v)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)} " \
               f"{m.group(4)}:{m.group(5)}:{m.group(6) or '00'}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def detect(path: Path) -> str | None:
    names = sheet_names_fast(path)
    if SHEET_LEADS not in names:
        return None
    for row in open_workbook(path)[SHEET_LEADS].iter_rows(values_only=True):
        cols = {(str(h).strip() if h is not None else "") for h in row}
        return SHEET_LEADS if all(k in cols for k in LEAD_KEYS) else None
    return None


def load(path: Path, cfg: Config, sheet: str | None = None) -> LoadResult:
    path = Path(path)
    wb = open_workbook(path)
    leads = _rows_of(wb[SHEET_LEADS])
    prods = _rows_of(wb[SHEET_PRODUCTS]) if SHEET_PRODUCTS in wb.sheetnames else []

    vat = cfg.vat_rate
    by_lead: dict[str, list[dict]] = {}
    for p in prods:
        key = util.norm(p.get("Lead ID"))
        if key:
            by_lead.setdefault(key, []).append(p)

    res = LoadResult(kind=SYSTEM, file_name=path.name, sheet=SHEET_LEADS,
                     rows_read=len(leads) + len(prods))
    st = {"rows_read": len(leads), "orders": 0, "product_lines": 0, "no_date": 0,
          "used_orig_value": 0, "used_lead_value": 0, "was_calibrated": 0,
          "masked_name": 0, "masked_address": 0, "no_phone": 0,
          "unmapped_sku": 0, "cancelled_flag": 0, "sources": {}}

    for row in leads:
        order_id = util.norm(row.get("Lead ID"))
        if not order_id:
            continue
        ts = _ts(row.get("Lead Purchase Date")) or _ts(row.get("Create Datetime"))
        if not ts:
            st["no_date"] += 1
            continue

        reported = _num(row.get("Lead Value")) or 0.0
        orig = _num(row.get("__origValue"))
        if orig is not None:
            raw_ex = orig
            raw_field = "__origValue (ยอดก่อนเกลี่ยที่แดชบอร์ดเดิมเก็บไว้)"
            st["used_orig_value"] += 1
            if abs(orig - reported) > 0.005:
                st["was_calibrated"] += 1
        else:
            raw_ex = reported
            raw_field = "Lead Value (ไม่มี __origValue = ใบนี้ไม่เคยถูกเกลี่ย ยอดที่เห็นคือยอดดิบ)"
            st["used_lead_value"] += 1

        source = util.norm(row.get("Source")) or "GoSell"
        st["sources"][source] = st["sources"].get(source, 0) + 1
        lead_intro = util.norm(row.get("Lead Intro"))
        owner = util.norm(row.get("Current User Name"))
        gs_channel = util.norm(row.get("GoSell Channel"))
        status = util.norm(row.get("GoSell Order Status"))

        name = _txt(row.get("Lead Name"))
        if not name and util.is_masked(row.get("Lead Name")):
            st["masked_name"] += 1
        address = _txt(row.get("GoSell Address"))
        if not address and util.is_masked(row.get("GoSell Address")):
            st["masked_address"] += 1
        phone = util.clean_phone(_txt(row.get("__cxPhone")) or _txt(row.get("GoSell Phone")),
                                 address)
        if not phone:
            st["no_phone"] += 1
        cancel_how = _txt(row.get("__cancelHow"))
        if cancel_how:
            st["cancelled_flag"] += 1

        res.orders.append({
            "order_id": order_id,
            "source": source,
            "order_date": util.biz_date_str(ts, cfg.biz_day_start_hour),
            "order_ts": ts,
            "channel": nz.channel_label(cfg, owner, lead_intro),
            "team": nz.team_of(cfg, owner, lead_intro),
            "owner": owner or "ไม่ระบุ",
            "payment_method": nz.payment_method_of(cfg, owner, source, lead_intro),
            "phone_raw": _txt(row.get("GoSell Phone")),
            "customer_key": util.customer_key(phone),
            "is_anonymous": 0 if phone else 1,
            "name_raw": name or order_id,
            "amount_ex_vat": util.rv(raw_ex),
            "amount_inc_vat": util.rv(raw_ex * (1 + vat)),
            "item_count": len(by_lead.get(order_id, [])),
            "status_raw": status,
            "lead_intro": lead_intro,
            "recipient_name": name,
            "phone_clean": phone,
            "address": address,
            "province": _txt(row.get("GoSell Province")) or (util.find_province_in_text(address) or ""),
            "postcode": _txt(row.get("GoSell Zipcode")) or (util.find_zip_in_text(address) or ""),
            "shipping_amount": 0.0,
            "is_free": 1 if util.rv(raw_ex) == 0 else 0,
            "page_key": nz.lead_page_key(lead_intro),
            "gosell_channel": gs_channel,
            "data_state": "raw",
        })
        for sysname, col in (("gosell", "GoSell Order ID"),
                             ("gosell_customer", "GoSell Customer ID")):
            ext = _txt(row.get(col))
            if ext:
                res.sources.append({"system": sysname, "external_id": ext, "order_id": order_id})

        # ---------------------------------------------------------- บรรทัดสินค้า
        lines = by_lead.get(order_id, [])
        for i, p in enumerate(lines):
            line_ex = _num(p.get("__origVal"))
            unit_ex = _num(p.get("__origUnit"))
            if line_ex is None:
                line_ex = _num(p.get("Value Before VAT")) or 0.0
            if unit_ex is None:
                unit_ex = _num(p.get("Unit Price")) or 0.0
            qty = _num(p.get("Quantity")) or 1
            sku = _txt(p.get("Product Code")) or _txt(p.get("GoSell Product SKU"))
            pname = util.norm(p.get("Product Name"))
            if not sku:
                st["unmapped_sku"] += 1
            res.items.append({
                "order_id": order_id,
                "line_seq": i,
                "product": nz.short_product_name(cfg, pname),
                "product_raw": pname,
                "sku": sku,
                "qty": qty,
                "unit_ex_vat": util.rv(unit_ex),
                "discount": _num(p.get("Discount")) or 0.0,
                "amount_ex_vat": util.rv(line_ex),
                "amount_inc_vat": util.rv(line_ex * (1 + vat)),
                "promotion": "",
                "source_file": path.name,
            })
            st["product_lines"] += 1

        res.audit.append({
            "order_id": order_id,
            "sheet": SHEET_LEADS,
            "source_row": row.get("__row"),
            "source_key": _txt(row.get("GoSell Order ID")) or order_id,
            "raw_inc_vat": util.rv(raw_ex * (1 + vat)),
            "raw_field": raw_field,
            "mapping": {
                # ยอดที่แดชบอร์ดเดิมโชว์ — เก็บไว้ "เทียบ" อย่างเดียว ไม่ได้เอาไปใช้เป็นยอด
                "dashboard_reported_ex_vat": util.rv(reported),
                "dashboard_reported_inc_vat": util.rv(reported * (1 + vat)),
                "dashboard_xl_bucket": _txt(row.get("__xlBucket")),
                "dashboard_adjusted": bool(orig is not None and abs(orig - reported) > 0.005),
                "source_col": source,
                "gosell_channel": gs_channel,
                "gosell_channel_name": util.norm(row.get("GoSell Channel Name")),
                "payment_raw": util.norm(row.get("GoSell Payment")),
                "channel_mapped_to": nz.channel_label(cfg, owner, lead_intro),
                "owner_raw": owner,
                "status_raw": status,
                "cancel_flag_in_file": cancel_how,
                "pii_masked_in_file": bool(util.is_masked(row.get("Lead Name"))
                                           or util.is_masked(row.get("GoSell Address"))),
                "product_lines": len(lines),
            },
        })
        st["orders"] += 1

    if not res.orders:
        raise ValueError(f"{path.name}: ไม่พบออเดอร์ที่ใช้ได้ในชีต {SHEET_LEADS}")
    res.stats = st
    return res
