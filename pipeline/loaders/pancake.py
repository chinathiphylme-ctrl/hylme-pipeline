# -*- coding: utf-8 -*-
"""อ่านไฟล์ export จาก Pancake

พอร์ตจาก index.html: isPancakeWorkbook + convertPancakeData
กติกาทุกข้อยกมาตรงตัว รวมทั้งเคสที่แก้ไว้แล้วในของเดิม:

  * forward-fill รหัสคำสั่งซื้อ — Pancake ใส่รหัสเฉพาะบรรทัดแรกของออเดอร์
    บรรทัดสินค้าถัดไปเว้นว่าง ถ้าไม่ลากรหัสลงมาสินค้าบรรทัดที่ 2+ จะหายไปทั้งหมด
  * ค่าส่ง = 'ยอดสั่งซื้อทั้งหมด' - 'มูลค่าการสั่งซื้อ' แล้วบวกกลับเข้าไปในยอดออเดอร์
  * ออเดอร์แจกฟรี (ส่วนลด 100%) ต้องเป็น 0 บาท ห้ามตกไปใช้ราคาเต็ม
    -> แยก 'มีคอลัมน์แต่เป็น 0' ออกจาก 'ไม่มีคอลัมน์' ด้วย field_of ที่คืน None
  * วันที่ของออเดอร์ = 'วันที่ยืนยันคำสั่งซื้อ' ไม่ใช่วันที่สร้าง
  * คอลัมน์ 'ชื่อ' กับ 'นามสกุล' ในไฟล์ Pancake สลับกันอยู่ ต้องสลับคืน
"""
from __future__ import annotations

import re
from pathlib import Path

from . import LoadResult, field_of, num_of, read_sheet_dicts, read_sheet_matrix, sheet_names
from .. import util
from ..config import Config
from .. import normalize as nz

SYSTEM = "pancake"
SOURCE_LABEL = "Pancake"

# ชื่อคอลัมน์ที่อาจสะกดต่างกันในแต่ละรุ่นของไฟล์ export
NET_KEYS = [
    "มูลค่าของออเดอร์หลังใช้ส่วนลด",
    "มูลค่าคำสั่งซื้อหลังหักส่วนลด",
    "มูลค่าคำสั่งซื้อหลังใช้ส่วนลด",
    "มูลค่าของออเดอร์หลังหักส่วนลด",
]
CONFIRM_DATE_KEYS = [
    "วันที่ยืนยันคำสั่งซื้อ", "วันยืนยันการสั่งซื้อ", "วันที่ยืนยันการสั่งซื้อ",
    "วันยืนยันคำสั่งซื้อ", "เวลายืนยันการสั่งซื้อ", "เวลายืนยันคำสั่งซื้อ",
]
CONFIRM_TIME_KEYS = [
    "เวลายืนยันการสั่งซื้อ", "เวลายืนยันคำสั่งซื้อ",
    "วันที่ยืนยันคำสั่งซื้อ", "วันยืนยันการสั่งซื้อ",
]
PRODUCT_KEYS = ["รหัสสินค้า", "สินค้า", "รหัสรุ่น", "รายละเอียดสินค้า"]


def detect(path: Path) -> str | None:
    """คืนชื่อชีตถ้าเป็นไฟล์ Pancake (index.html: isPancakeWorkbook)"""
    for sn in sheet_names(path):
        aoa = read_sheet_matrix(path, sn, max_rows=5)
        for row in aoa:
            cells = [re.sub(r"\s+", " ", str("" if c is None else c)).strip() for c in row]
            if ("รหัสคำสั่งซื้อ" in cells and "สถานะ" in cells
                    and ("มูลค่าของออเดอร์หลังใช้ส่วนลด" in cells or "รหัสสินค้า" in cells)):
                return sn
    return None


def _person_name(row: dict) -> tuple[str, str, str]:
    """สลับคืนชื่อ-นามสกุลที่ Pancake สลับคอลัมน์กันไว้ (index.html: pkPersonName)

    คอลัมน์ 'ชื่อ' เก็บนามสกุลจริง และคอลัมน์ 'นามสกุล' เก็บชื่อจริง
    ยืนยันจากคอลัมน์ 'ชื่อผู้รับ' ที่เรียงถูกอยู่แล้ว
    """
    col_name = str(field_of(row, ["ชื่อ"]) or "").strip()        # จริง ๆ คือนามสกุล
    col_surname = str(field_of(row, ["นามสกุล"]) or "").strip()  # จริง ๆ คือชื่อ
    if col_name and col_surname:
        return col_surname, col_name, f"{col_surname} {col_name}".strip()
    only = col_surname or col_name
    return only, "", only


def _channel_to_intro(src) -> str:
    """'Facebook / Hylme ...' -> 'Facebook : Hylme ...' (index.html: pkChannelToIntro)"""
    s = util.norm(src)
    if not s:
        return ""
    return re.sub(r"\s*/\s*", " : ", s, count=1)


def load(path: Path, cfg: Config, sheet: str | None = None) -> LoadResult:
    path = Path(path)
    sheet = sheet or detect(path)
    if not sheet:
        raise ValueError(f"{path.name}: ไม่ใช่ไฟล์ Pancake (หาคอลัมน์ 'รหัสคำสั่งซื้อ' ไม่พบ)")

    rows = read_sheet_dicts(path, sheet)
    vat = cfg.vat_rate
    res = LoadResult(kind="pancake", file_name=path.name, sheet=sheet, rows_read=len(rows))
    st = {
        "rows_read": len(rows), "orders": 0, "skipped_cancel": 0, "skipped_empty": 0,
        "lines": 0, "unmapped_sku": 0, "used_confirm_date": 0, "date_shifted": 0,
        "free_orders": 0, "free_list_value": 0.0, "carried_lines": 0,
    }

    # ---- จัดกลุ่มบรรทัดเป็นออเดอร์ พร้อม forward-fill รหัสคำสั่งซื้อ ----
    grouped: dict[str, list[dict]] = {}
    last_oid = ""
    for r in rows:
        oid_raw = field_of(r, ["รหัสคำสั่งซื้อ"])
        has_product = field_of(r, PRODUCT_KEYS) is not None
        key = "" if oid_raw is None else str(oid_raw).strip()
        if key:
            last_oid = key
        elif has_product and last_oid:
            key = last_oid
            st["carried_lines"] += 1
        if not key or not has_product:
            st["skipped_empty"] += 1
            continue
        grouped.setdefault(key, []).append(r)

    for oid, lines in grouped.items():
        head = lines[0]
        status = str(field_of(head, ["สถานะ"]) or "").strip()
        if cfg.re_pancake_cancel.search(status):
            st["skipped_cancel"] += 1
            continue

        # ---- ค่าส่งที่เก็บจากลูกค้า ----
        pk_gross_all = num_of(head, ["ยอดสั่งซื้อทั้งหมด"])   # ราคาเต็ม + ค่าส่ง
        pk_list_val = num_of(head, ["มูลค่าการสั่งซื้อ"])      # ราคาเต็ม ไม่รวมค่าส่ง
        shipping = max(0.0, util.rv(pk_gross_all - pk_list_val)) if (pk_gross_all > 0 and pk_list_val > 0) else 0.0

        # ---- ยอดสุทธิที่เก็บจากลูกค้าจริง ----
        net_raw = field_of(head, NET_KEYS)
        has_net = net_raw is not None and str(net_raw).strip() != ""
        order_net = num_of(head, NET_KEYS)
        is_free = 0
        if order_net > 0:
            order_net = util.rv(order_net + shipping)
        elif has_net:
            # ไฟล์ระบุชัดว่าหลังหักส่วนลด = 0 -> แจกฟรี 100%
            # ถ้ายังเก็บค่าส่งปลายทาง ให้นับเฉพาะเงินที่เก็บจริง (COD)
            order_net = num_of(head, ["COD"])
            if order_net <= 0:
                order_net = 0.0
                is_free = 1
                st["free_orders"] += 1
                st["free_list_value"] = util.rv(st["free_list_value"] + pk_gross_all)
        else:
            # ไฟล์ไม่มีคอลัมน์ 'หลังหักส่วนลด' -> ถอยไปหาแหล่งอื่นตามลำดับเดิม
            # COD รวมค่าส่งมาแล้ว จึงไม่บวกซ้ำ
            if order_net <= 0:
                order_net = num_of(head, ["COD"])
            if order_net <= 0:
                order_net = util.rv(pk_list_val + shipping)
            if order_net <= 0:
                order_net = sum(num_of(r, ["ราคา"]) for r in lines)
        lead_value_pre = util.rv(order_net / (1 + vat))

        # ---- วันที่: ใช้วันที่ยืนยันคำสั่งซื้อ ----
        confirm_dt = util.pk_datetime(field_of(head, CONFIRM_DATE_KEYS),
                                      field_of(head, CONFIRM_TIME_KEYS))
        created_dt = util.pk_datetime(field_of(head, ["วันที่สร้างคำสั่งซื้อ"]),
                                      field_of(head, ["เวลาสร้างคำสั่งซื้อ"]))
        dt = confirm_dt or created_dt
        if confirm_dt:
            st["used_confirm_date"] += 1
            if created_dt and confirm_dt[:10] != created_dt[:10]:
                st["date_shifted"] += 1
        if not dt:
            st["skipped_empty"] += 1
            continue

        owner = nz.map_pancake_rep(cfg, field_of(head, ["พนักงานยืนยัน", "คนจัดการ", "พนักงานอัพเดท", "ผู้สร้าง"]))
        channel_intro = _channel_to_intro(
            field_of(head, ["แหล่งที่มาของการสั่งซื้อ (ชื่อ)", "แหล่งที่มาของคำสั่งซื้อ", "Chat page"])
        )
        pay_word = "COD" if num_of(head, ["COD"]) > 0 else "โอน"
        lead_intro = (channel_intro + ", " if channel_intro else "") + pay_word

        name = str(field_of(head, ["ชื่อผู้รับ", "ลูกค้า"]) or "").strip()
        if not name:
            name = _person_name(head)[2]
        if not name or re.search(r"deleted", name, re.I):
            name = "ออเดอร์ " + oid

        order_id = "PK-" + oid
        phone_raw = str(field_of(head, ["เบอร์โทรลูกค้า", "หมายเลขโทรศัพท์"]) or "").strip()
        addr = str(field_of(head, ["ที่อยู่", "เลขที่บ้าน ซอย"]) or "").strip()
        province_field = str(field_of(head, ["จังหวัด/เมือง", "รหัสจังหวัด"]) or "").strip()
        zip_field = str(field_of(head, ["รหัสไปรษณีย์", "Postal code"]) or "").strip()
        province = province_field or (util.find_province_in_text(addr) or "")
        postcode = zip_field or (util.find_zip_in_text(addr) or "")
        phone_clean = util.clean_phone(phone_raw, addr)

        res.orders.append({
            "order_id": order_id,
            "source": SOURCE_LABEL,
            "order_date": util.biz_date_str(dt, cfg.biz_day_start_hour),
            "order_ts": dt,
            "channel": nz.channel_label(cfg, owner, lead_intro),
            "team": nz.team_of(cfg, owner, lead_intro),
            "owner": owner,
            "payment_method": nz.payment_method_of(cfg, owner, SOURCE_LABEL, lead_intro),
            "phone_raw": phone_raw,
            "customer_key": util.customer_key(phone_clean or phone_raw),
            "is_anonymous": 0 if phone_clean else 1,
            "name_raw": name,
            "amount_ex_vat": lead_value_pre,
            "amount_inc_vat": util.rv(order_net),
            "item_count": len(lines),
            "status_raw": status,
            "lead_intro": lead_intro,
            "recipient_name": name,
            "phone_clean": phone_clean,
            "address": addr,
            "province": province,
            "postcode": postcode,
            "shipping_amount": shipping,
            "is_free": is_free,
            "page_key": nz.lead_page_key(lead_intro),
            "data_state": "raw",   # อ่านจากไฟล์ต้นทางโดยตรง = ยอดดิบ
        })
        res.sources.append({"system": SYSTEM, "external_id": oid, "order_id": order_id})

        # ---- บรรทัดสินค้า: เกลี่ยยอดสุทธิลงแต่ละบรรทัดตามสัดส่วนราคา ----
        line_gross = []
        for r in lines:
            g = num_of(r, ["ราคา"])
            if g <= 0:
                g = num_of(r, ["ราคาต่อหน่วย"]) * (num_of(r, ["จำนวน"]) or 1)
            line_gross.append(g)
        total_gross = sum(line_gross) or len(lines)
        total_disc_pre = util.rv(num_of(head, ["ส่วนลด"]) / (1 + vat))

        acc = 0.0
        acc_disc = 0.0
        for i, r in enumerate(lines):
            st["lines"] += 1
            qty = round(num_of(r, ["จำนวน"])) or 1
            is_last = (i == len(lines) - 1)
            share = util.rv(lead_value_pre - acc) if is_last else util.rv(lead_value_pre * (line_gross[i] / total_gross))
            acc = util.rv(acc + share)
            disc_share = util.rv(total_disc_pre - acc_disc) if is_last else util.rv(total_disc_pre * (line_gross[i] / total_gross))
            acc_disc = util.rv(acc_disc + disc_share)

            sku = str(field_of(r, ["รหัสสินค้า"]) or "").strip()
            raw_name = str(field_of(r, ["สินค้า", "รหัสรุ่น"]) or "").strip()
            pname = nz.map_product_name(cfg, sku, raw_name)
            if not sku:
                st["unmapped_sku"] += 1
            if nz.is_shipping_line(cfg, pname):
                continue
            item = {
                "order_id": order_id,
                "line_seq": i,
                "product": nz.short_product_name(cfg, pname),
                "product_raw": pname,
                "sku": sku,
                "qty": qty,
                "unit_ex_vat": util.rv(share / qty),
                "discount": max(0.0, disc_share),
                "amount_ex_vat": share,
                "amount_inc_vat": util.rv(share * (1 + vat)),
                "source_file": path.name,
            }
            item["promotion"] = nz.promo_label(cfg, item)
            res.items.append(item)

        st["orders"] += 1

    res.stats = st
    return res
