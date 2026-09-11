# -*- coding: utf-8 -*-
"""อ่านไฟล์ export จาก MyCloud — ครั้งเดียวได้ครบทุกอย่าง

ของเดิมใน index.html อ่านไฟล์นี้ 3 รอบด้วย 3 ฟังก์ชัน:
    convertMyCloudData()      ออเดอร์ที่คีย์เข้า MC เอง
    convertMarketplaceData()  ออเดอร์ Shopee / Lazada / TikTok
    scanCancelWorkbook()      ทะเบียนออเดอร์ยกเลิก
และผู้ใช้ต้องอัปไฟล์เดิมซ้ำ 2 ครั้งผ่าน 2 ปุ่ม (ขั้นตอนที่ 12 และ 13 ของ workflow เดิม)

ไฟล์นี้อ่านรอบเดียวแล้วแยกออกเป็น 4 อย่าง: ออเดอร์ · บรรทัดสินค้า · เหตุการณ์จัดส่ง · ทะเบียนยกเลิก

--------------------------------------------------------------------------
กติกาการตั้ง order_id (จุดที่ต่างจากของเดิม — ตั้งใจแก้)
--------------------------------------------------------------------------
ของเดิม ออเดอร์ marketplace หนึ่งใบอาจถูกสร้างเป็นทั้ง 'MC-xxx' (จาก convertMyCloudData)
และ 'MKP-xxx' (จาก convertMarketplaceData) แล้วค่อยไปหักล้างกันด้วยการเทียบยอดเงิน
(mkpTakeAmountMatch) ซึ่งเป็นการกันซ้ำที่พึ่งตัวเลขบังเอิญตรงกัน

ที่นี่แยกให้ขาดตั้งแต่ต้น หนึ่งแถว = หนึ่งออเดอร์เท่านั้น:
    1. Shop Order No ขึ้นต้นด้วย 'PK-'  -> ออเดอร์นี้มาจาก Pancake อยู่แล้ว
                                          ไม่สร้างใหม่ แต่ผูกข้อมูลจัดส่งเข้ากับออเดอร์เดิม
                                          (ของเดิมข้ามทิ้งเฉย ๆ ทำให้ไม่มีข้อมูลการส่ง)
    2. ช่องทางเป็น marketplace          -> 'MKP-' + Sales Channel Order No | Shop Order No | Order No
    3. นอกนั้น                           -> 'MC-'  + Shop Order No | Sales Channel Order No
    4. ไม่มีเลขทั้งคู่                     -> ข้าม (ออเดอร์ที่ทีมคีย์เข้า MC เอง นับจาก Pancake ไปแล้ว)

ผลที่ต้องได้เท่าเดิม: จำนวนออเดอร์และยอดขายรวมต้องตรงกับแดชบอร์ดเดิม
"""
from __future__ import annotations

import re
from pathlib import Path

from . import LoadResult, read_sheet_dicts, read_sheet_matrix, sheet_names
from .. import util
from ..config import Config
from .. import normalize as nz

SHEET_RE = re.compile(r"outbound[-_ ]?orders", re.I)
HEADER_KEYS = ["Shop Sale Channel Name", "Order Total Price", "Shop Order No"]

SYSTEM_MC = "mycloud"
SYSTEM_MKP = {"Shopee": "shopee", "Lazada": "lazada", "Tiktok": "tiktok"}

SOURCE_MC = "MyCloud"
SOURCE_MKP = "Marketplace (MyCloud)"


def find_sheet(path: Path) -> str | None:
    """index.html: mcFindSheet — หาจากชื่อชีตก่อน ถ้าไม่เจอค่อยดูจากหัวคอลัมน์"""
    names = sheet_names(path)
    for n in names:
        if SHEET_RE.search(n):
            return n
    for n in names:
        aoa = read_sheet_matrix(path, n, max_rows=1)
        head = [util.norm(c) for c in (aoa[0] if aoa else [])]
        if all(k in head for k in HEADER_KEYS):
            return n
    return None


def detect(path: Path) -> str | None:
    return find_sheet(path)


def _row_sku(r: dict) -> str:
    """index.html: mcRowSku — Shop SKU ก่อน (ตรงกับฝั่ง Pancake) แล้วค่อย MyCloud SKU"""
    shop = util.norm(r.get("Shop SKU"))
    if shop and not re.fullmatch(r"n/?a", shop, re.I):
        return shop.upper()
    mine = util.norm(r.get("MyCloud SKU"))
    if mine and not re.fullmatch(r"n/?a", mine, re.I):
        return mine.upper()
    return ""


def _group_orders(rows: list[dict], key_of) -> tuple[list[dict], int, int]:
    """จับกลุ่มแถวเป็นออเดอร์ (index.html: mcGroupOrders)

    ชีต '-expand' มี 1 แถว = 1 SKU ออเดอร์เดียวกันจึงซ้ำหลายแถว — ไม่ใช่ข้อมูลซ้ำ
    """
    groups: dict[str, dict] = {}
    dup_rows = 0
    no_key = 0
    for r in rows:
        key = key_of(r)
        if not key or re.fullmatch(r"n/?a", key, re.I):
            no_key += 1
            continue
        g = groups.get(key)
        is_new = g is None
        if is_new:
            g = {"key": key, "head": r, "lines": []}
            groups[key] = g
        sku = _row_sku(r)
        if sku:
            g["lines"].append({
                "sku": sku,
                "name": util.norm(r.get("Product Name")),
                "qty": round(util.num(r.get("Product Quantity"))),
                "unit": util.num(r.get("Product Unit Price")),
            })
        elif not is_new:
            dup_rows += 1        # แถวซ้ำจริง (ชีตแบบเดิมที่ไม่มีคอลัมน์สินค้า)
    return list(groups.values()), dup_rows, no_key


def _build_items(cfg: Config, order_id: str, key: str, gross_pre: float,
                 lines: list[dict], file_name: str) -> list[dict]:
    """กระจายยอดที่เก็บจากลูกค้าจริงลงแต่ละ SKU (index.html: mcBuildProdRows)

    ไม่ใช้ Product Unit Price ตรง ๆ เพราะเป็นราคา list ของ listing ไม่ใช่ราคาขายจริง
    (Shopee: list 189,059 แต่เก็บจริง 55,409) จึงใช้เป็นแค่ 'น้ำหนัก' แบ่งสัดส่วน
    บรรทัดสุดท้ายรับเศษ เพื่อให้ผลรวมเท่ายอดออเดอร์พอดี
    """
    if not lines:
        return []
    w = [(l["qty"] or 0) * (l["unit"] or 0) for l in lines]
    total_w = sum(w)
    if total_w <= 0:
        w = [l["qty"] or 0 for l in lines]
        total_w = sum(w)
    if total_w <= 0:
        w = [1] * len(lines)
        total_w = len(lines)

    out = []
    acc = 0.0
    for i, l in enumerate(lines):
        share = util.rv(gross_pre - acc) if i == len(lines) - 1 else util.rv(gross_pre * (w[i] / total_w))
        acc = util.rv(acc + share)
        pname = nz.map_product_name(cfg, l["sku"], l["name"])
        out.append({
            "order_id": order_id,
            "line_seq": i,
            "product": nz.short_product_name(cfg, pname),
            "product_raw": pname,
            "sku": l["sku"],
            "qty": l["qty"],
            "unit_ex_vat": util.rv(share / (l["qty"] or 1)),
            # Discount = 0 โดยตั้งใจ: ราคา list ของ Shopee/Lazada เชื่อไม่ได้
            # ถ้าเอามาลบเป็นส่วนลด การ์ดโปรโมชันจะขึ้น 'ลดพิเศษ' เกินจริงเกือบทุกออเดอร์
            "discount": 0.0,
            "amount_ex_vat": share,
            "amount_inc_vat": util.rv(share * (1 + cfg.vat_rate)),
            "promotion": "",
            "source_file": file_name,
        })
    return out


def _events_from(cfg: Config, r: dict, order_id: str, file_name: str) -> list[dict]:
    """สร้างเหตุการณ์การจัดส่งจากคอลัมน์วันที่ของ MyCloud

    เก็บเป็น event log ไม่ใช่สถานะล่าสุดแถวเดียว — ออเดอร์หนึ่งใบผ่านหลายสถานะได้
    และเป็นเหตุผลที่ระบบเดิมต้องมีทะเบียนยกเลิกแยกต่างหาก
    """
    out = []
    statuses = (r.get("Order Status"), r.get("MKP Order Status"), r.get("Shipment Status"))

    def add(when, status):
        d = util.mc_datetime(when)
        if d and status:
            out.append({"order_id": order_id, "event_date": d[:10],
                        "status": status, "source": "mycloud", "note": file_name})

    add(r.get("Delivered At"), "delivered")
    add(r.get("Shipped At"), "in_transit")
    std = nz.delivery_status(cfg, *statuses)
    if std in ("cancelled", "returned", "refused", "lost"):
        when = (util.mc_datetime(r.get("Updated At")) or util.mc_datetime(r.get("Delivered At"))
                or util.mc_datetime(r.get("Shipped At")) or util.mc_datetime(r.get("Created At")))
        if when:
            out.append({"order_id": order_id, "event_date": when[:10], "status": std,
                        "source": "mycloud", "note": file_name})
    return out


def load(path: Path, cfg: Config, sheet: str | None = None) -> LoadResult:
    path = Path(path)
    sheet = sheet or find_sheet(path)
    if not sheet:
        raise ValueError(f"{path.name}: ไม่พบชีต 'outbound-orders' — ต้องเป็นไฟล์ export จาก MyCloud")

    rows = read_sheet_dicts(path, sheet)
    vat = cfg.vat_rate
    res = LoadResult(kind="mycloud", file_name=path.name, sheet=sheet, rows_read=len(rows))
    st = {
        "rows_read": len(rows), "orders_mc": 0, "orders_mkp": 0, "linked_pancake": 0,
        "skipped_cancel": 0, "skipped_no_date": 0, "skipped_no_key": 0, "dup_in_file": 0,
        "gross_total": 0.0, "channels": {}, "unknown_status": {}, "cancels_found": 0,
        "events": 0,
    }

    # จับกลุ่มด้วยลำดับที่ครอบคลุมที่สุด แล้วค่อยตัดสินใจเรื่อง id ทีหลัง
    groups, dup_rows, no_key = _group_orders(
        rows, lambda r: util.pick(r.get("Sales Channel Order No"), r.get("Shop Order No"), r.get("Order No"))
    )
    st["dup_in_file"] = dup_rows
    st["skipped_no_key"] = no_key

    for grp in groups:
        r = grp["head"]
        key = grp["key"]
        shop_no = util.norm(r.get("Shop Order No"))
        chan_no = util.pick(r.get("Sales Channel Order No"))
        mc_no = util.norm(r.get("Order No"))
        raw_chan = r.get("Shop Sale Channel Name")
        is_mkp = bool(cfg.re_mkp_channel.search(util.norm(raw_chan)))

        statuses = [util.norm(x) for x in (r.get("Order Status"), r.get("MKP Order Status"), r.get("Shipment Status"))]
        cancelled = any(cfg.re_mc_cancel.search(s) for s in statuses if s)

        dt = (util.mc_datetime(r.get("Created At")) or util.mc_datetime(r.get("Paid At"))
              or util.mc_datetime(r.get("Instructed At")))
        date_only = util.biz_date_str(dt, cfg.biz_day_start_hour) if dt else None
        gross = util.num(r.get("Order Total Price"))

        # ---------- (1) ทะเบียนยกเลิก — เก็บก่อนเสมอ ไม่ว่าออเดอร์จะถูกนับหรือไม่ ----------
        if cancelled:
            st["cancels_found"] += 1
            res.cancels.append({
                "cancel_key": key,
                "mc_no": mc_no,
                "shop_no": shop_no,
                "chan_no": chan_no,
                "channel": nz.mc_channel_name(cfg, raw_chan) or cfg.channels["unknown_channel_label"],
                "cancel_date": date_only,
                "cancel_gross": gross,
                "phone": util.cx_phone(r.get("Shipping Phone")),
                "name": util.cx_name(r.get("Shipping Name")),
                "status_raw": " / ".join(s for s in statuses if s and not re.fullmatch(r"n/?a", s, re.I)),
                "source": "mycloud",
                "file_name": path.name,
            })

        # ---------- (2) ออเดอร์ที่มาจาก Pancake — ผูกข้อมูลจัดส่ง ไม่สร้างออเดอร์ใหม่ ----------
        if re.match(r"^PK-", shop_no, re.I):
            st["linked_pancake"] += 1
            res.sources.append({"system": SYSTEM_MC, "external_id": mc_no, "order_id": shop_no.upper()})
            ev = _events_from(cfg, r, shop_no.upper(), path.name)
            res.events.extend(ev)
            st["events"] += len(ev)
            res.orders.append({
                "_update_only": True,          # อัปเดตเฉพาะฟิลด์จัดส่งของออเดอร์ที่มีอยู่แล้ว
                "order_id": shop_no.upper(),
                "mc_order_no": mc_no,
                "tracking_no": util.norm(r.get("Tracking Number")),
            })
            continue

        # ---------- (3) ออเดอร์ที่ต้องนับเป็นยอดขาย ----------
        if cancelled:
            st["skipped_cancel"] += 1
            continue
        for s in statuses:
            k = s.lower()
            if k and k not in cfg.mc_ok_status:
                st["unknown_status"][s] = st["unknown_status"].get(s, 0) + 1

        if not dt:
            st["skipped_no_date"] += 1
            continue

        if is_mkp:
            order_id = "MKP-" + key
            source = SOURCE_MKP
            owner = cfg.mappings["marketplace_rep"]
            st["orders_mkp"] += 1
        else:
            mc_key = util.pick(r.get("Shop Order No"), r.get("Sales Channel Order No"))
            if not mc_key:
                # ออเดอร์ที่ทีมคีย์เข้า MC เอง (Facebook / LINE / Telesales / CRM)
                # นับจากไฟล์ Pancake/ReadyPlanet ไปแล้ว ดึงเข้ามาอีกจะซ้ำ
                st["skipped_no_key"] += 1
                continue
            order_id = "MC-" + mc_key
            source = SOURCE_MC
            owner = cfg.mappings["marketplace_rep"]
            st["orders_mc"] += 1

        lead_value_pre = util.rv(gross / (1 + vat))
        st["gross_total"] = util.rv(st["gross_total"] + gross)
        chan_name = nz.mc_channel_name(cfg, raw_chan) or cfg.channels["unknown_channel_label"]
        st["channels"][chan_name] = st["channels"].get(chan_name, 0) + 1

        lead_intro = nz.mc_lead_intro(cfg, raw_chan)
        recipient = util.norm(r.get("Shipping Name"))
        if not recipient or re.fullmatch(r"n/?a", recipient, re.I):
            recipient = "ออเดอร์ " + key
        addr = util.norm(r.get("Shipping Address"))
        zip_raw = util.norm(r.get("Shipping Postcode"))
        postcode = zip_raw if re.fullmatch(r"\d{5}", zip_raw or "") else (util.find_zip_in_text(addr) or "")
        phone_raw = util.norm(r.get("Shipping Phone"))
        phone_clean = "" if util.is_masked(phone_raw) else util.clean_phone(phone_raw, addr)

        res.orders.append({
            "order_id": order_id,
            "source": source,
            "order_date": date_only,
            "order_ts": dt,
            "channel": nz.channel_label(cfg, owner, lead_intro),
            "team": nz.team_of(cfg, owner, lead_intro),
            "owner": owner,
            "payment_method": nz.payment_method_of(cfg, owner, source, lead_intro),
            "phone_raw": phone_raw,
            "customer_key": util.customer_key(phone_clean) if phone_clean else None,
            "is_anonymous": 0 if phone_clean else 1,
            "name_raw": recipient,
            "amount_ex_vat": lead_value_pre,
            "amount_inc_vat": util.rv(gross),
            "item_count": len(grp["lines"]) or 1,
            "status_raw": util.norm(r.get("Order Status")),
            "lead_intro": lead_intro,
            "recipient_name": recipient,
            "phone_clean": phone_clean,
            "address": addr,
            "province": util.find_province_in_text(addr) or "",
            "postcode": postcode,
            "mc_order_no": mc_no,
            "tracking_no": util.norm(r.get("Tracking Number")),
            "shipping_amount": 0.0,
            "is_free": 0,
            "page_key": nz.lead_page_key(lead_intro),
            "data_state": "raw",   # อ่านจากไฟล์ต้นทางโดยตรง = ยอดดิบ
        })

        # เลขอ้างอิงของทุกระบบที่รู้จัก — ทำให้ join กลับไปหาออเดอร์เดิมได้เสมอ
        res.sources.append({"system": SYSTEM_MC, "external_id": mc_no, "order_id": order_id})
        if chan_no:
            res.sources.append({
                "system": SYSTEM_MKP.get(chan_name, chan_name.lower()),
                "external_id": chan_no, "order_id": order_id,
            })
        if shop_no and shop_no != mc_no:
            res.sources.append({"system": "shop_order_no", "external_id": shop_no, "order_id": order_id})

        res.items.extend(_build_items(cfg, order_id, key, lead_value_pre, grp["lines"], path.name))
        ev = _events_from(cfg, r, order_id, path.name)
        res.events.extend(ev)
        st["events"] += len(ev)

    res.stats = st
    return res
