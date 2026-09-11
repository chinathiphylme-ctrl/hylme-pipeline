# -*- coding: utf-8 -*-
"""อ่านไฟล์ export จาก GoSell (ข้อมูลเก่า พ.ย. 2025 – พ.ค. 2026)

พอร์ตจาก index.html: isGoSellWorkbook / findGoSellHeader / convertGoSellData (บรรทัด 1675–1808)
พร้อมตัวช่วย gsNorm · gsNum · gsFirstNonEmpty · gsDate · gsPhone · gsProductSku · gsIsShipping

--------------------------------------------------------------------------
ทำไมต้องมี loader ตัวนี้
--------------------------------------------------------------------------
ยอด ม.ค.–มิ.ย. ที่อยู่ในฐานข้อมูลตอนนี้เป็น `pre_reconciled` — มาจากไฟล์ที่แดชบอร์ด
export ออกมาหลังเกลี่ยแล้ว ไม่ใช่ยอดดิบ ชั้นกระทบยอดจึงข้ามทั้งหมด
พอโหลดไฟล์ GoSell ต้นฉบับเข้ามา ออเดอร์ `GS-*` จะถูกแทนที่ด้วยยอดดิบจริง
(`data_state = 'raw'`) แล้วชั้นเกลี่ยถึงจะทำงานได้เต็มรูปแบบ

--------------------------------------------------------------------------
กติกาที่ยกมาทั้งหมด
--------------------------------------------------------------------------
* หัวตารางอยู่แถวไหนก็ได้ใน 12 แถวแรก — เจอคีย์ครบ 5 จาก 7 ตัวถือว่าใช่
* 1 ออเดอร์กระจายอยู่หลายแถว จับกลุ่มด้วย "เลขที่คำสั่งซื้อ"
* ยอดระดับออเดอร์อยู่บรรทัดเดียว -> gsFirstNonEmpty เอาค่าแรกที่ไม่ว่าง
* ยอดก่อน VAT: ใช้ "มูลค่าก่อนภาษี" ถ้ามีค่า > 0 ไม่งั้นถอดจาก "รวมทั้งสิ้น" / 1.07
* สถานะยกเลิก -> ข้ามทั้งออเดอร์ | สถานะส่งคืนสำเร็จ -> นับไว้ แต่ยังโหลดเข้ามา
  (การตัดออกจากยอดขายเป็นหน้าที่ของ RETURNED_RE ตอนรวมยอด ไม่ใช่ของ loader)
* บรรทัดค่าส่งไม่นับเป็นสินค้า
* SKU: ใช้ "รหัสสินค้า (SKU)" ก่อน ถ้าไม่มีค่อยเดาจากชื่อสินค้า -> บันทึกไว้ใน audit ว่าเดามา
* เกลี่ยยอดก่อน VAT ลงบรรทัดสินค้าตามสัดส่วนยอดรวมสินค้า บรรทัดสุดท้ายรับเศษ

`GoSell Channel` ถูกเก็บไว้ในคอลัมน์ `orders.gosell_channel` เพราะชั้นเกลี่ยใช้จับช่องทาง
ของรายงานขาย (index.html: XL_GS_MAP)
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from . import LoadResult, read_sheet_matrix, sheet_names
from .. import normalize as nz
from .. import util
from ..config import Config

SYSTEM = "gosell"
SOURCE_LABEL = "GoSell"

HEADER_KEYS = ["ช่องทางการขาย", "เลขที่คำสั่งซื้อ", "หมายเลขลูกค้า", "ลูกค้า",
               "สถานะคำสั่งซื้อ", "ชื่อสินค้า", "วันที่สั่งซื้อ"]
MIN_HEADER_HITS = 5

RE_CANCEL = re.compile(r"ยกเลิก|cancel", re.I)
RE_RETURNED = re.compile(r"ส่งคืนสำเร็จ|returned|return", re.I)
RE_SHIP_NAME = re.compile(r"ค่าส่ง|ค่าจัดส่ง|shipping|delivery fee", re.I)
RE_SHIP_TYPE = re.compile(r"ค่าขนส่ง|ค่าจัดส่ง", re.I)

# เดา SKU จากชื่อสินค้า เมื่อไฟล์ไม่มีรหัส (index.html: gsProductSku)
SKU_GUESS = [
    (re.compile(r"daily oils|daily oil|oils blend|dob|เดลี่", re.I), "HM-DOB-01"),
    (re.compile(r"magnesium|แมกนีเซียม|mag\b|แมก", re.I), "HM-MNS-01"),
    (re.compile(r"gluta|collagen|กลูต้า|คอลลาเจน", re.I), "HM-GT-01"),
    (re.compile(r"olive|โอลีฟ|มะกอก", re.I), "HM-OL-01"),
    (re.compile(r"gaba|กาบา|กาบ้า", re.I), "HM-GB-01"),
]


def _num(v):
    """index.html: gsNum — คืน None เมื่อไม่มีค่า (ต่างจาก 0 ที่เป็นค่าจริง)"""
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(re.sub(r"[฿$,]", "", str(v)).strip())
    except ValueError:
        return None


def _first(group: list[dict], col: str):
    """ค่าแรกที่ไม่ว่างและไม่ใช่ '-' ในกลุ่มแถวของออเดอร์เดียวกัน (index.html: gsFirstNonEmpty)"""
    for r in group:
        v = r.get(col)
        if v is not None and util.norm(v) and util.norm(v) != "-":
            return v
    return ""


def _date(v) -> str:
    """index.html: gsDate"""
    if isinstance(v, (_dt.datetime, _dt.date)):
        d = v
        if isinstance(d, _dt.datetime) and (d.hour or d.minute or d.second):
            return f"{d.year}-{str(d.month).zfill(2)}-{str(d.day).zfill(2)} " \
                   f"{str(d.hour).zfill(2)}:{str(d.minute).zfill(2)}:{str(d.second).zfill(2)}"
        return f"{d.year}-{str(d.month).zfill(2)}-{str(d.day).zfill(2)}"
    s = util.norm(v)
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        out = f"{m.group(1)}-{str(int(m.group(2))).zfill(2)}-{str(int(m.group(3))).zfill(2)}"
        if m.group(4):
            out += f" {str(int(m.group(4))).zfill(2)}:{m.group(5)}:{m.group(6) or '00'}"
        return out
    return ""


def _phone(v) -> str:
    """index.html: gsPhone"""
    s = util.norm(v)
    d = util.norm_phone_digits(s)
    if util.is_thai_phone(d):
        return d
    e = util.extract_thai_phone(s)
    return e if util.is_thai_phone(e) else ""


def _guess_sku(name, raw_sku) -> tuple[str, bool]:
    """คืน (sku, เดามาหรือไม่) — index.html: gsProductSku"""
    sku = util.norm(raw_sku)
    if sku and sku != "-":
        return sku, False
    s = util.norm(name)
    for pattern, code in SKU_GUESS:
        if pattern.search(s):
            return code, True
    return "", False


def _is_shipping(name, type_) -> bool:
    return bool(RE_SHIP_NAME.search(util.norm(name)) or RE_SHIP_TYPE.search(util.norm(type_)))


def _find_header(path: Path) -> tuple[str, int] | None:
    """หาชีตและแถวหัวตาราง (index.html: findGoSellHeader)"""
    for sn in sheet_names(path):
        aoa = read_sheet_matrix(path, sn, max_rows=12)
        for i, row in enumerate(aoa):
            h = [util.norm(x) for x in (row or [])]
            if sum(1 for k in HEADER_KEYS if k in h) >= MIN_HEADER_HITS:
                return sn, i
    return None


def detect(path: Path) -> str | None:
    hit = _find_header(path)
    return hit[0] if hit else None


def load(path: Path, cfg: Config, sheet: str | None = None) -> LoadResult:
    path = Path(path)
    hit = _find_header(path)
    if not hit:
        raise ValueError(f"{path.name}: ไม่พบหัวตาราง GoSell "
                         f"(ต้องมีอย่างน้อย {MIN_HEADER_HITS} จาก {len(HEADER_KEYS)} คอลัมน์หลัก)")
    sheet_name, header_row = hit
    aoa = read_sheet_matrix(path, sheet_name)
    cols = [util.norm(x) for x in (aoa[header_row] or [])]

    # เก็บเลขแถวจริงไว้ด้วย เพื่อให้ audit ชี้กลับไปที่แถวใน Excel ได้
    rows: list[tuple[int, dict]] = []
    for i in range(header_row + 1, len(aoa)):
        r = aoa[i] or []
        d = {cols[j]: (r[j] if j < len(r) else None) for j in range(len(cols)) if cols[j]}
        if util.norm(d.get("เลขที่คำสั่งซื้อ")):
            rows.append((i + 1, d))       # i+1 = เลขแถวตามที่เห็นใน Excel

    vat = cfg.vat_rate
    grouped: dict[str, list[tuple[int, dict]]] = {}
    for rownum, d in rows:
        grouped.setdefault(util.norm(d["เลขที่คำสั่งซื้อ"]), []).append((rownum, d))

    res = LoadResult(kind="gosell", file_name=path.name, sheet=sheet_name, rows_read=len(rows))
    st = {"rows_read": len(rows), "orders": 0, "cancelled": 0, "returned": 0,
          "customers": 0, "product_lines": 0, "unmapped_sku": 0, "guessed_sku": 0,
          "no_items": 0, "no_date": 0}
    customers: set[str] = set()

    for order_no, group in grouped.items():
        rownums = [rn for rn, _ in group]
        g = [d for _, d in group]
        status = util.norm(_first(g, "สถานะคำสั่งซื้อ"))
        if RE_CANCEL.search(status):
            st["cancelled"] += 1
            continue
        if RE_RETURNED.search(status):
            st["returned"] += 1

        customer_id = util.norm(_first(g, "หมายเลขลูกค้า"))
        if customer_id:
            customers.add(customer_id)
        name = util.norm(_first(g, "ลูกค้า")) or order_no
        phone = _phone(_first(g, "เบอร์โทร1")) or _phone(_first(g, "เบอร์โทร2"))
        address = util.norm(_first(g, "ที่อยู่"))
        province = util.norm(_first(g, "จังหวัด"))
        zipcode = util.norm(_first(g, "รหัสไปรษณีย์"))
        channel = util.norm(_first(g, "ช่องทางการขาย")) or util.norm(_first(g, "ชื่อช่องทางการขาย"))
        channel_name = util.norm(_first(g, "ชื่อช่องทางการขาย"))
        payment = util.norm(_first(g, "การชำระเงิน"))
        rep = util.norm(_first(g, "พนักงานขาย"))
        dt = _date(_first(g, "วันที่สั่งซื้อ")) or _date(_first(g, "วันที่ชำระเงิน"))
        if not dt:
            st["no_date"] += 1
            continue

        # ยอดระดับออเดอร์ — ใช้ "มูลค่าก่อนภาษี" ถ้ามีค่ามากกว่า 0 ไม่งั้นถอดจาก "รวมทั้งสิ้น"
        pre_candidates = [v for v in (_num(d.get("มูลค่าก่อนภาษี")) for d in g) if v is not None]
        incl_candidates = [v for v in (_num(d.get("รวมทั้งสิ้น")) for d in g) if v is not None]
        if incl_candidates:
            order_incl = incl_candidates[0]
            incl_field = "รวมทั้งสิ้น"
        else:
            alt = [v for v in (_num(d.get("ยอดรวมคำสั่งซื้อ")) for d in g) if v is not None]
            order_incl = alt[0] if alt else 0.0
            incl_field = "ยอดรวมคำสั่งซื้อ" if alt else "(ไม่พบ)"
        pre_positive = next((v for v in pre_candidates if v > 0), None)
        if pre_positive is not None:
            order_pre = pre_positive
            pre_field = "มูลค่าก่อนภาษี"
        else:
            order_pre = order_incl / (1 + vat)
            pre_field = f"{incl_field} / {1 + vat}"

        order_id = "GS-" + order_no
        lead_intro = ", ".join(x for x in (channel, payment) if x)
        phone_clean = phone or util.clean_phone("", address)

        res.orders.append({
            "order_id": order_id,
            "source": SOURCE_LABEL,
            "order_date": util.biz_date_str(dt, cfg.biz_day_start_hour),
            "order_ts": dt,
            "channel": nz.channel_label(cfg, rep, lead_intro),
            "team": nz.team_of(cfg, rep, lead_intro),
            "owner": rep or "ไม่ระบุ",
            "payment_method": nz.payment_method_of(cfg, rep, SOURCE_LABEL, lead_intro),
            "phone_raw": phone,
            "customer_key": util.customer_key(phone_clean),
            "is_anonymous": 0 if phone_clean else 1,
            "name_raw": name,
            "amount_ex_vat": util.rv(order_pre),
            "amount_inc_vat": util.rv(order_pre * (1 + vat)),
            "item_count": len(g),
            "status_raw": status,
            "lead_intro": lead_intro,
            "recipient_name": name,
            "phone_clean": phone_clean,
            "address": address,
            "province": province or (util.find_province_in_text(address) or ""),
            "postcode": zipcode or (util.find_zip_in_text(address) or ""),
            "shipping_amount": 0.0,
            "is_free": 1 if util.rv(order_pre) == 0 else 0,
            "page_key": nz.lead_page_key(lead_intro),
            "gosell_channel": channel,
            "data_state": "raw",
        })
        if customer_id:
            res.sources.append({"system": "gosell_customer", "external_id": customer_id,
                                "order_id": order_id})
        res.sources.append({"system": SYSTEM, "external_id": order_no, "order_id": order_id})

        # ---- บรรทัดสินค้า ----
        items = [d for d in g
                 if util.norm(d.get("ชื่อสินค้า")) and util.norm(d.get("ชื่อสินค้า")) != "-"
                 and not _is_shipping(d.get("ชื่อสินค้า"), d.get("ประเภทสินค้า"))
                 and (_num(d.get("จำนวนสินค้า")) or 0) > 0]
        bases = []
        for d in items:
            qty = _num(d.get("จำนวนสินค้า")) or 1
            unit = _num(d.get("ราคาต่อชิ้น"))
            line = _num(d.get("ยอดรวมสินค้า"))
            bases.append(line if (line is not None and line >= 0)
                         else (unit * qty if (unit is not None and unit >= 0) else 0.0))
        base_total = sum(bases)

        guessed = []
        allocated = 0.0
        for i, d in enumerate(items):
            qty = _num(d.get("จำนวนสินค้า")) or 1
            sku, was_guessed = _guess_sku(d.get("ชื่อสินค้า"), d.get("รหัสสินค้า (SKU)"))
            if was_guessed:
                st["guessed_sku"] += 1
                guessed.append({"name": util.norm(d.get("ชื่อสินค้า")), "sku": sku})
            if not sku:
                st["unmapped_sku"] += 1
            share = (util.rv(order_pre - allocated) if i == len(items) - 1
                     else util.rv(order_pre * (bases[i] / base_total) if base_total > 0
                                  else order_pre / len(items)))
            allocated = util.rv(allocated + share)
            pname = util.norm(d.get("ชื่อสินค้า"))
            res.items.append({
                "order_id": order_id,
                "line_seq": i,
                "product": nz.short_product_name(cfg, pname),
                "product_raw": pname,
                "sku": sku or util.norm(d.get("รหัสสินค้า (SKU)")),
                "qty": qty,
                "unit_ex_vat": util.rv(share / qty) if qty else share,
                "discount": _num(d.get("ส่วนลดต่อรายการ")) or 0.0,
                "amount_ex_vat": share,
                "amount_inc_vat": util.rv(share * (1 + vat)),
                "promotion": "",
                "source_file": path.name,
            })
            st["product_lines"] += 1
        if not items:
            st["no_items"] += 1

        res.audit.append({
            "order_id": order_id,
            "sheet": sheet_name,
            "source_row": rownums[0],
            "source_key": order_no,
            "raw_inc_vat": util.rv(order_pre * (1 + vat)),
            "raw_field": pre_field,
            "mapping": {
                "rows_in_file": rownums,
                "src_pre_vat": pre_positive,          # ตัวเลขที่ไฟล์เขียนไว้จริง
                "src_incl_vat": order_incl if incl_candidates or order_incl else None,
                "status_raw": status,
                "gosell_channel": channel,
                "gosell_channel_name": channel_name,
                "payment_raw": payment,
                "channel_mapped_to": nz.channel_label(cfg, rep, lead_intro),
                "owner_raw": rep,
                "customer_id": customer_id,
                "sku_guessed_from_name": guessed,
                "returned": bool(RE_RETURNED.search(status)),
            },
        })
        st["orders"] += 1

    st["customers"] = len(customers)
    res.stats = st
    return res
