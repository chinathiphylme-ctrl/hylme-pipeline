# -*- coding: utf-8 -*-
"""อ่านไฟล์ Google Sheet เก่า (1 ไฟล์ = 1 เดือน, ชีตชื่อ 1–31 = วันของเดือนนั้น)

พอร์ตจาก index.html: convertOldData (บรรทัด 3229) + handleOldFiles (3327)
พร้อมตัวช่วย parseThaiMonthYear · mapRecorder · mapOldProduct · parseOldPromo ·
oldChannelToIntro · parseDelivery และกติกากันซ้ำ dropOldSheetDays (3298)

--------------------------------------------------------------------------
ทำไมต้องมี loader ตัวนี้
--------------------------------------------------------------------------
ยอด ม.ค.–มิ.ย. ที่อยู่ในฐานตอนนี้เป็น `pre_reconciled` (ยอดที่ผ่านการเกลี่ยมาแล้ว)
ไฟล์ Google Sheet เก่าคือ "ยอดดิบ" ตัวจริงของช่วงนั้นสำหรับออเดอร์ที่ทีมคีย์มือ
เมื่อโหลดเข้ามา ออเดอร์จะถูกเขียนใหม่เป็น `data_state = 'raw'` ชั้นกระทบยอดจึงทำงานได้

--------------------------------------------------------------------------
กติกาที่ยกมาทั้งหมด (ห้ามเปลี่ยน — มีผลกับยอดขายโดยตรง)
--------------------------------------------------------------------------
* เดือน/ปี อ่านจาก "ชื่อไฟล์" (ชื่อเดือนไทย + ปี พ.ศ.) ไม่ได้อ่านจากในไฟล์
* แถวที่ไม่มีทั้ง "โปรโมชั่น" และ "สินค้า" -> ไม่ใช่ออเดอร์ ข้ามไป
* ยอดขาย "ติดลบ" (คืนของ/ปรับยอด) -> ข้าม   ยอดขาย "0 บาท" -> นับเป็นออเดอร์จริง
  (เบิกภายใน ของแถม แลกสิทธิ์วันเกิด แคมเปญแจกฟรี — เดิมเคยโดน `sale <= 0` ตัดทิ้ง)
* "ยอดขาย Sale" เป็นราคารวม VAT แล้ว -> เก็บ Lead Value เป็นยอดก่อน VAT (หาร 1.07)
* แตกสินค้าจากคอลัมน์ "โปรโมชั่น" เช่น "Daily Oils Blend 2 กป, Mag 1 กป"
  ถ้าแตกไม่ได้เลย ค่อยถอยไปใช้คอลัมน์ "สินค้า"
* เกลี่ยยอดก่อน VAT ลงบรรทัดสินค้าตามสัดส่วน "จำนวน" บรรทัดสุดท้ายรับเศษ
* เวลาในวันไม่มีในไฟล์ -> สร้างขึ้นเรียงตามลำดับแถว (08:00 เป็นต้นไป นาทีละใบ)
  เพื่อให้เรียงลำดับภายในวันได้ ไม่ได้แปลว่าเป็นเวลาจริง

--------------------------------------------------------------------------
กันข้อมูลซ้ำ
--------------------------------------------------------------------------
Lead ID ของข้อมูลเก่าเป็น "เลขลำดับ" ที่คำนวณใหม่ทุกครั้งที่แปลงไฟล์ ถ้าจำนวนแถวเปลี่ยน
เลขลำดับจะเลื่อนทั้งไฟล์ -> กันซ้ำด้วย order_id ไม่ได้ จึงกันซ้ำด้วย "วันที่" แทน:
วันไหนที่ไฟล์ใหม่มีข้อมูล ให้ลบออเดอร์ Source = 'Google Sheet (เก่า)' ของวันนั้นออกก่อน
ส่งผ่าน LoadResult.replace_scope ไปให้ daily_pipeline เรียก db.drop_orders_by_scope()
ไฟล์จากระบบอื่น (Pancake / MyCloud / Marketplace / GoSell) ไม่ถูกแตะต้อง
"""
from __future__ import annotations

import re
from pathlib import Path

from . import LoadResult, open_workbook, sheet_names_fast
from .. import normalize as nz
from .. import util
from ..config import Config

SYSTEM = "oldsheet"
SOURCE_LABEL = "Google Sheet (เก่า)"

THAI_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}
MONTH_NAME = {v: k for k, v in THAI_MONTHS.items()}

# ชื่อเล่นผู้บันทึก -> ชื่อเต็ม (ให้ตรงกับข้อมูลใหม่); ที่ไม่รู้จักใช้ชื่อเดิม
RECORDER_TO_FULLNAME = {
    "นุ่น": "กานต์ปริยา ช่างเกิด", "แซน": "ธันญณัฐภ์ น้อยปุก",
    "คิว": "จิรัชฐ์โชติ สะอาดยิ่ง", "ก้อย": "ชิตานุช จิตรานนท์",
}

# คอลัมน์ที่บ่งบอกว่าเป็นไฟล์เก่า (ใช้ตอน detect)
MARKER_COLUMNS = ["โปรโมชั่น", "ยอดขาย Sale", "ยอดขาย", "ข้อมูลการจัดส่ง", "ผู้บันทึก",
                  "ช่องทางสั่งซื้อ", "ช่องทางชำระ", "ชื่อ Social"]

RE_PROMO_QTY = re.compile(r"^(.*?)\s*(\d+)\s*(?:กป|กระปุก|ขวด|ชิ้น|กล่อง|pcs?)?\s*$", re.I)
RE_NAME_TAIL = re.compile(r"(โทร|tel|เบอร์)\.?\s*$", re.I)


# ---------------------------------------------------------------- ตัวช่วย
def parse_thai_month_year(filename) -> tuple[int, int] | None:
    """index.html: parseThaiMonthYear — คืน (month, year ค.ศ.)"""
    s = str(filename or "")
    month = next((v for k, v in THAI_MONTHS.items() if k in s), None)
    year = None
    be = re.search(r"25\d\d", s)
    if be:
        year = int(be.group(0)) - 543
    if not year:
        ce = re.search(r"20\d\d", s)
        if ce:
            year = int(ce.group(0))
    return (month, year) if (month and year) else None


def map_recorder(name) -> str:
    k = str(name or "").strip()
    return RECORDER_TO_FULLNAME.get(k, k)


def map_old_product(name) -> tuple[str, str]:
    """index.html: mapOldProduct — คืน (sku, ชื่อเต็ม) ลำดับการตรวจสำคัญ ห้ามสลับ"""
    s = str(name or "").lower()
    if "olive" in s or "โอลีฟ" in s or "มะกอก" in s:
        return "HM-OL-01", "Hylme Organic Extra Virgin Olive Oil"
    if ("daily oil" in s or "daliy oil" in s or "oils blend" in s
            or "dob" in s or "เดลี่" in s):
        return "HM-DOB-01", "Hylme Daily Oils Blend 60 Softgel"
    if "mag" in s or "แมก" in s:
        return "HM-MNS-01", "Hylme Magnesium Glycinate"
    if "gluta" in s or "กลูต้า" in s or "คอลลาเจน" in s:
        return "HM-GT-01", "Hylme Gluta Collagen"
    if "gaba" in s or "กาบา" in s or "กาบ้า" in s:
        return "HM-GB-01", "Hylme GABA"
    return "", "Hylme " + str(name or "").strip()


def parse_old_promo(promo_text, fallback_product, fallback_qty) -> list[dict]:
    """แยกสินค้า+จำนวนจากคอลัมน์ "โปรโมชั่น" (index.html: parseOldPromo)"""
    items: list[dict] = []
    for part in [x.strip() for x in re.split(r"[,\n]", str(promo_text or "")) if x.strip()]:
        m = RE_PROMO_QTY.match(part)
        if m and m.group(1).strip():
            nm = m.group(1).strip()
            try:
                qty = int(m.group(2)) or 1
            except ValueError:
                qty = 1
        else:
            num = re.search(r"\d+", part)
            nm = re.sub(r"\d+", "", part).strip()
            qty = (int(num.group(0)) or 1) if num else 1
        sku, full = map_old_product(nm)
        items.append({"sku": sku, "name": full, "qty": qty, "raw": part})
    if not items and fallback_product:
        names = [x.strip() for x in re.split(r"[,\n]", str(fallback_product)) if x.strip()]
        if len(names) == 1:
            sku, full = map_old_product(names[0])
            items.append({"sku": sku, "name": full, "qty": fallback_qty or 1, "raw": names[0]})
        else:
            for n in names:
                sku, full = map_old_product(n)
                items.append({"sku": sku, "name": full, "qty": 1, "raw": n})
    return items


def old_channel_to_intro(ch) -> str:
    """index.html: oldChannelToIntro"""
    s = str(ch or "").strip()
    if not s:
        return ""
    if re.search(r"shopee", s, re.I):
        return "Shopee"
    if re.search(r"tiktok", s, re.I):
        return "Tiktok"
    if re.search(r"lazada", s, re.I):
        return "Lazada"
    if re.search(r"telesales", s, re.I):
        return "Telesales"
    if re.search(r"line", s, re.I) or "ไลน์" in s:
        return "LINE : Hylme"
    if re.search(r"instagram", s, re.I):
        return "Instagram : Hylme"
    if re.search(r"hylme|เพจ|ครบจบ|mag|gluta", s, re.I):
        return "Facebook : " + re.sub(r"^เพจ\s*", "", s).strip()
    return s          # CRM / Other ฯลฯ เก็บข้อความไว้ (กราฟแสดงเป็น "ไม่ระบุช่องทาง")


def parse_delivery(text, social) -> dict:
    """index.html: parseDelivery — แยกชื่อ/เบอร์/ที่อยู่ ออกจากข้อความก้อนเดียว"""
    raw = str(text or "").replace("\r", "")
    phone = util.extract_thai_phone(raw)          # ใช้ตัวจับเบอร์ตัวเดียวกับทั้งระบบ
    lines = [x.strip() for x in raw.split("\n") if x.strip()]
    first_line = lines[0] if lines else ""
    name = RE_NAME_TAIL.sub("", re.split(r"\s+\d", first_line)[0]).strip()
    if not name:
        name = str(social or "").strip()
    address = re.sub(r"\s+", " ", raw.replace("\n", " ")).strip()
    return {"name": name, "phone": phone, "address": address}


def _get(row: dict, names: list[str]):
    """index.html: getField — เทียบชื่อคอลัมน์แบบ trim อย่างเดียว"""
    for n in names:
        for k, v in row.items():
            if str(k).strip() == n:
                return v
    return None


def _p2(n) -> str:
    return str(n).zfill(2)


def _day_sheets_of(names: list[str]) -> list[tuple[int, str]]:
    """ชีตที่ชื่อเป็นเลข 1–31 — เรียงตามลำดับในไฟล์ เพื่อให้เลขลำดับ Lead ID คงที่"""
    out = []
    for sn in names:
        m = re.match(r"^\s*(\d{1,2})", str(sn).strip())
        if m:
            day = int(m.group(1))
            if 1 <= day <= 31:
                out.append((day, sn))
    return out


def _header_of(ws) -> list[str]:
    """อ่านเฉพาะแถวแรกของชีต (ไฟล์จริงหนัก 10-15 MB ห้ามอ่านทั้งชีตตอน detect)"""
    for row in ws.iter_rows(values_only=True):
        return [(str(h).strip() if h is not None else "") for h in row]
    return []


def _rows_of(ws) -> list[dict]:
    """อ่านทั้งชีตเป็น list ของ dict โดยใช้แถวแรกเป็นหัวคอลัมน์ (เทียบเท่า sheet_to_json)"""
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if header is None:
        return []
    cols = [(str(h).strip() if h is not None else "") for h in header]
    out = []
    for r in it:
        if r is None or all(v is None for v in r):
            continue
        out.append({cols[i]: r[i] for i in range(min(len(cols), len(r))) if cols[i]})
    return out


# ---------------------------------------------------------------- detect / load
def detect(path: Path) -> str | None:
    """เป็นไฟล์เก่าเมื่อ ชื่อชีตเป็นเลขวัน + ชีตนั้นมีคอลัมน์เฉพาะของไฟล์เก่าอย่างน้อย 3 คอลัมน์

    ชื่อชีตเป็นเลขอย่างเดียวยังไม่พอ (ไฟล์อื่นก็ตั้งชื่อชีตเป็นเลขได้) ตัวชี้ขาดคือชุดคอลัมน์
    ตรวจหลายชีตเพราะชีตต้น ๆ ของเดือนอาจว่าง
    เปิดไฟล์ครั้งเดียวแล้ววนดูหัวตาราง — ไฟล์จริงหนัก 10-15 MB × 31 ชีต
    """
    if not _day_sheets_of(sheet_names_fast(path)):
        return None                       # ด่านแรกราคาถูก ไม่ต้องเปิดไฟล์
    wb = open_workbook(path)
    for _, sn in _day_sheets_of(list(wb.sheetnames))[:8]:
        cols = set(_header_of(wb[sn]))
        if not cols:
            continue
        hits = sum(1 for c in MARKER_COLUMNS if c in cols)
        has_product = ("โปรโมชั่น" in cols) or ("สินค้า" in cols)
        if hits >= 3 and has_product:
            return sn
    return None


def load(path: Path, cfg: Config, sheet: str | None = None) -> LoadResult:
    path = Path(path)
    my = parse_thai_month_year(path.name)
    if not my:
        raise ValueError(
            f"{path.name}: ไม่ทราบว่าเป็นข้อมูลเดือนอะไร\n"
            f"  ไฟล์เก่าไม่ได้เก็บเดือน/ปีไว้ข้างใน ต้องอ่านจากชื่อไฟล์\n"
            f"  แก้ชื่อไฟล์ให้มีชื่อเดือนไทย + ปี พ.ศ. เช่น  ยอดขาย กรกฎาคม 2569.xlsx"
        )
    month, year = my
    return _load_wb(open_workbook(path), path, cfg, month, year)


def _load_wb(wb, path: Path, cfg: Config, month: int, year: int) -> LoadResult:
    days = _day_sheets_of(list(wb.sheetnames))
    if not days:
        raise ValueError(f"{path.name}: ไม่พบชีตที่เป็นวันของเดือน (ชื่อชีตควรเป็นเลข 1–31)")

    vat = cfg.vat_rate
    file_tag = f"เก่า: {MONTH_NAME.get(month, month)} {year + 543}"
    res = LoadResult(kind="oldsheet", file_name=path.name,
                     sheet=", ".join(sn for _, sn in days))
    st = {"rows_read": 0, "sheets": len(days), "orders": 0, "skipped": 0,
          "skipped_negative": 0, "zero_value": 0, "no_items": 0,
          "product_lines": 0, "unmapped_sku": 0, "file_tag": file_tag,
          "month": f"{year}-{_p2(month)}"}
    seen_days: set[str] = set()

    for day, sn in days:
        rows = _rows_of(wb[sn])
        st["rows_read"] += len(rows)
        idx = 0                                    # ลำดับ "ภายในวัน" ใช้สร้างเวลาเทียม
        for rownum, row in enumerate(rows, start=2):   # แถว 1 = หัวตาราง
            promo = _get(row, ["โปรโมชั่น"])
            prod_cat = _get(row, ["สินค้า"])
            if not util.norm(promo) and not util.norm(prod_cat):
                continue
            sale = util.num(_get(row, ["ยอดขาย Sale", "ยอดขาย"]))
            if sale < 0:                           # คืนของ/ปรับยอด ไม่ใช่ออเดอร์ใหม่
                st["skipped_negative"] += 1
                st["skipped"] += 1
                continue
            if sale == 0:                          # ออเดอร์แจกฟรี = ออเดอร์จริง ต้องนับ
                st["zero_value"] += 1
            qty_total = util.num(_get(row, ["จำนวน"]))
            items = parse_old_promo(promo, prod_cat, int(qty_total) if qty_total else 0)
            if not items:
                st["skipped"] += 1
                st["no_items"] += 1
                continue

            seq = st["orders"] + 1                 # เลขลำดับนับต่อเนื่อง "ทั้งไฟล์"
            order_id = f"OLD-{year}{_p2(month)}{_p2(day)}-{_p2(seq)}"
            hh = min(23, 8 + idx // 60)
            dt = f"{year}-{_p2(month)}-{_p2(day)} {_p2(hh)}:{_p2(idx % 60)}:00"
            idx += 1

            value_pre = util.rv(sale / (1 + vat))
            pay_raw = util.norm(_get(row, ["ช่องทางชำระ"]))
            if re.search(r"โอน", pay_raw):
                pay_word = "โอน"
            elif re.search(r"cod|ปลายทาง", pay_raw, re.I):
                pay_word = "COD"
            else:
                pay_word = pay_raw
            intro_ch = old_channel_to_intro(_get(row, ["ช่องทางสั่งซื้อ"]))
            # ใส่คอมมาหลังช่องทางเสมอ เพื่อให้ parse_channel ตัดชื่อช่องทางได้ถูก
            lead_intro = ((intro_ch + ", ") if intro_ch else "") + (pay_word or "") \
                         + ((" - " + util.norm(promo)) if util.norm(promo) else "")
            d = parse_delivery(_get(row, ["ข้อมูลการจัดส่ง"]), _get(row, ["ชื่อ Social"]))
            owner = map_recorder(_get(row, ["ผู้บันทึก"]))
            order_date = util.biz_date_str(dt, cfg.biz_day_start_hour)
            seen_days.add(order_date)

            res.orders.append({
                "order_id": order_id,
                "source": SOURCE_LABEL,
                "order_date": order_date,
                "order_ts": dt,
                "channel": nz.channel_label(cfg, owner, lead_intro),
                "team": nz.team_of(cfg, owner, lead_intro),
                "owner": owner or "ไม่ระบุ",
                "payment_method": nz.payment_method_of(cfg, owner, SOURCE_LABEL, lead_intro),
                "phone_raw": d["phone"],
                "customer_key": util.customer_key(d["phone"]),
                "is_anonymous": 0 if d["phone"] else 1,
                "name_raw": d["name"] or order_id,
                "amount_ex_vat": value_pre,
                "amount_inc_vat": util.rv(value_pre * (1 + vat)),
                "item_count": len(items),
                "status_raw": "",
                "lead_intro": lead_intro,
                "recipient_name": d["name"],
                "phone_clean": d["phone"],
                "address": d["address"],
                "province": util.find_province_in_text(d["address"]) or "",
                "postcode": util.find_zip_in_text(d["address"]) or "",
                "shipping_amount": 0.0,
                "is_free": 1 if value_pre == 0 else 0,
                "page_key": nz.lead_page_key(lead_intro),
                "gosell_channel": "",
                "data_state": "raw",
            })
            res.sources.append({"system": SYSTEM, "external_id": f"{path.name}#{sn}#{rownum}",
                                "order_id": order_id})

            # ---- เกลี่ยยอดก่อน VAT ลงบรรทัดสินค้าตามสัดส่วนจำนวน บรรทัดสุดท้ายรับเศษ ----
            tot_qty = sum(i["qty"] or 0 for i in items) or len(items)
            acc = 0.0
            unmapped = []
            for i, it in enumerate(items):
                share = (util.rv(value_pre - acc) if i == len(items) - 1
                         else util.rv(value_pre * (it["qty"] / tot_qty)))
                acc = util.rv(acc + share)
                q = it["qty"] or 1
                if not it["sku"]:
                    st["unmapped_sku"] += 1
                    unmapped.append(it["raw"])
                res.items.append({
                    "order_id": order_id,
                    "line_seq": i,
                    "product": nz.short_product_name(cfg, it["name"]),
                    "product_raw": it["name"],
                    "sku": it["sku"],
                    "qty": q,
                    "unit_ex_vat": util.rv(share / q),
                    "discount": 0.0,
                    "amount_ex_vat": share,
                    "amount_inc_vat": util.rv(share * (1 + vat)),
                    "promotion": util.norm(promo),
                    "source_file": path.name,
                })
                st["product_lines"] += 1

            res.audit.append({
                "order_id": order_id,
                "sheet": sn,
                "source_row": rownum,
                "source_key": f"{sn}#{rownum}",
                "raw_inc_vat": util.rv(value_pre * (1 + vat)),
                "raw_field": "ยอดขาย Sale (รวม VAT) / 1.07 -> เก็บก่อน VAT",
                "mapping": {
                    "file_tag": file_tag,
                    "month_from_filename": f"{year}-{_p2(month)}",
                    "day_sheet": sn,
                    "src_sale_inc_vat": sale,
                    "order_ts_synthetic": dt,
                    "seq_in_file": seq,
                    "promo_raw": util.norm(promo),
                    "product_col_raw": util.norm(prod_cat),
                    "items_parsed": [{"sku": i["sku"], "name": i["name"], "qty": i["qty"],
                                      "from": i["raw"]} for i in items],
                    "unmapped_sku": unmapped,
                    "channel_raw": util.norm(_get(row, ["ช่องทางสั่งซื้อ"])),
                    "channel_mapped_to": nz.channel_label(cfg, owner, lead_intro),
                    "payment_raw": pay_raw,
                    "recorder_raw": util.norm(_get(row, ["ผู้บันทึก"])),
                    "zero_value": sale == 0,
                },
            })
            st["orders"] += 1

    if not res.orders:
        raise ValueError(f"{path.name}: แปลงแล้วไม่พบออเดอร์ที่ใช้ได้ในไฟล์นี้")

    # กันซ้ำด้วย "วันที่" ไม่ใช่ order_id (index.html: dropOldSheetDays)
    res.replace_scope = {"source": SOURCE_LABEL, "dates": sorted(seen_days)}
    st["days_replaced"] = len(seen_days)
    res.stats = st
    return res
