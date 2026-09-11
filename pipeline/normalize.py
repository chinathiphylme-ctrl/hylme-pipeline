# -*- coding: utf-8 -*-
"""กฎธุรกิจสำหรับจัดหมวดหมู่ — พอร์ตจาก index.html

ทุกฟังก์ชันมีคู่ของมันใน index.html และต้องให้ผลเหมือนกัน
ค่าคงที่ทั้งหมดมาจาก config/*.json ไม่มีการฝังค่าไว้ในโค้ดนี้
"""
from __future__ import annotations

import re

from .config import Config
from . import util

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0000FE0F]", flags=re.UNICODE
)


# ---------------------------------------------------------------- สินค้า

def short_product_name(cfg: Config, name) -> str:
    """ยุบชื่อสินค้าให้เหลือ 'ชนิด' เดียว (index.html: shortProductName)

    ชื่อจาก GoSell/marketplace เป็นชื่อโพสต์ขาย ทีมตั้งใหม่ทุกแคมเปญ
    ตารางนี้ตัดข้อความการตลาดและขนาดแพ็กทิ้ง
    """
    base = _EMOJI_RE.sub("", str(name or ""))
    base = re.sub(r"^Hylme\s+", "", base, flags=re.I).strip()
    if not base:
        return cfg.products["name_unknown"]
    has_s9 = bool(cfg.re_s9.search(base))
    has_v9 = bool(cfg.re_v9.search(base))
    if has_s9 and has_v9:                       # เซ็ตรวม ต้องเช็คก่อนสินค้าเดี่ยว
        return cfg.products["name_s9_v9_set"]
    for pattern, canon in cfg.product_aliases:
        if pattern.search(base):
            return canon
    if has_s9:
        return cfg.products["name_s9"]
    if has_v9:
        return cfg.products["name_v9"]
    return cfg.products["name_unknown"]


def map_product_name(cfg: Config, sku, raw_name) -> str:
    """SKU -> ชื่อสินค้ามาตรฐาน (index.html: mapPancakeProductName)"""
    s = str(sku or "").strip().upper()
    mapped = cfg.products["sku_to_name"].get(s)
    if mapped:
        return mapped
    cleaned = re.sub(r"^\([^)]*\)\s*", "", str(raw_name or "")).strip()
    return cleaned or s or "สินค้า"


def is_shipping_line(cfg: Config, name) -> bool:
    """บรรทัดค่าส่ง ไม่ใช่สินค้า (index.html: isShippingLine)"""
    if not name:
        return False
    return str(name).strip().lower() in cfg.shipping_labels


def is_preorder_item(cfg: Config, sku, product_name) -> bool:
    """index.html: isPreorderItem"""
    if str(sku or "").strip().upper() in cfg.preorder_skus:
        return True
    nm = str(product_name or "").lower()
    return any(k.lower() in nm for k in cfg.products["preorder_name_keywords"])


def is_olive_oil(cfg: Config, sku, product_name) -> bool:
    """index.html: isOliveOil"""
    if str(sku or "").strip().upper() in cfg.olive_skus:
        return True
    nm = str(product_name or "").lower()
    return any(k in nm for k in cfg.products["olive_name_keywords"])


def is_sku_approved(cfg: Config, sku) -> bool:
    """SKU นี้ให้ออกเป็น Fulfill หรือไม่ (index.html: isSkuApproved)

    ค่าเริ่มต้นเดิม = false ทุกตัว (ทุก SKU เริ่มที่ OnHold)
    ตั้ง sku_approved_override ใน config/products.json เพื่อไม่ต้องมาติ๊กเองทุกวัน
    """
    s = str(sku or "").strip().upper()
    override = cfg.products.get("sku_approved_override") or {}
    if s in override:
        return bool(override[s])
    return bool(cfg.products.get("sku_default_approved", False))


def promo_label(cfg: Config, item: dict) -> str:
    """ป้ายโปรโมชันของบรรทัดสินค้า (index.html: promoLabel)"""
    q = round(util.num(item.get("qty")))
    disc = util.num(item.get("discount"))
    val = util.num(item.get("amount_ex_vat"))
    tag = "แจกฟรี" if (disc > 0 and val <= 0) else ("ลดพิเศษ" if disc > 0 else "ราคาปกติ")
    return f"{item.get('product')} ซื้อ {q} ชิ้น ({tag})"


# ---------------------------------------------------------------- ช่องทาง

def parse_channel(intro_text) -> str:
    """ดึงช่องทางออกจากข้อความ Lead Intro (index.html: parseChannel)

    'Facebook : Hylme ครบจบเรื่องไขมัน, COD - Olive Oil 3'
        -> 'Facebook Hylme ครบจบเรื่องไขมัน'
    """
    s = str(intro_text or "")
    m = re.search(r"(Facebook|Instagram)\s*:\s*([^\n,]+)", s, re.I)
    if m:
        platform = m.group(1)[0].upper() + m.group(1)[1:].lower()
        name = m.group(2).strip()
        return f"{platform} {name}" if name else platform
    if re.search(r"shopee", s, re.I):
        return "Shopee"
    if re.search(r"tiktok", s, re.I):
        return "Tiktok"
    if re.search(r"lazada", s, re.I):
        return "Lazada"
    if re.search(r"telesales|(^|[ ,])call($|[ ,])", s, re.I):
        return "Telesales"
    if re.search(r"crm", s, re.I):
        return "CRM"
    if re.search(r"line|ไลน์", s, re.I):
        return "LINE"
    if re.search(r"woocommerce|website|web", s, re.I):
        return "Website"
    return "ไม่ระบุช่องทาง"


def canon_channel_label(cfg: Config, raw) -> str:
    """รวมช่องทางที่เขียนต่างกันให้เป็นป้ายเดียว (index.html: canonChannelLabel)"""
    ch = str(raw or "")
    if re.search(r"instagram", ch, re.I):
        return "Instagram"
    if "ไขมัน" in ch:
        return "Facebook Hylme ครบจบเรื่องไขมัน"
    if "ผิว" in ch:
        return "Facebook Hylme ครบจบเรื่องผิว"
    if "นอน" in ch:
        return "Facebook Hylme ครบจบเรื่องนอน"
    if re.search(r"facebook|^fb\b", ch, re.I):
        return "Facebook Hylme"
    if re.search(r"line|ไลน์", ch, re.I):
        return cfg.channels["crm_channel_label"]      # LINE ทั้ง Privilege/Official = ทีม CRM
    if re.search(r"telesales", ch, re.I):
        return cfg.channels["telesales_channel_label"]
    return re.sub(r"\s+", " ", ch).strip() or cfg.channels["unknown_channel_label"]


def channel_label(cfg: Config, owner: str, lead_intro: str) -> str:
    """ช่องทางของออเดอร์ (index.html: channelLabel)

    เพิ่มจากของเดิม: ถ้าออเดอร์ไม่มี Lead Intro เลยและระบุช่องทางไม่ได้จริง ๆ
    จะถอยมาใช้ "ช่องทางประจำของผู้ดูแล" จาก config/mappings.json -> owner_default_channel
    ใช้เป็นทางสำรองชั้นสุดท้ายเท่านั้น ถ้ามีข้อมูลช่องทางอยู่แล้วจะไม่ถูกแตะ
    """
    person = util.norm(owner)
    if person in cfg.telesales_people:
        return cfg.channels["telesales_channel_label"]
    label = canon_channel_label(cfg, parse_channel(lead_intro))
    if label == cfg.channels["unknown_channel_label"] and not util.norm(lead_intro):
        fallback = cfg.owner_default_channel.get(person)
        if fallback:
            return fallback
    return label


def is_non_product_line(cfg: Config, name) -> bool:
    """บรรทัดที่ไม่ใช่สินค้า — ปรับเศษระดับบิล / ส่วนลดระดับบิล / ค่าธรรมเนียม

    บรรทัดพวกนี้ไม่มี SKU เป็นเรื่องปกติ ไม่ใช่ข้อมูลขาด
    เทียบแบบ "มีคำนี้อยู่ในชื่อ" เพราะต้นทางใส่ขีดคร่อมมาด้วย เช่น "— ส่วนต่างระดับบิล —"
    """
    s = str(name or "").lower()
    return any(lbl in s for lbl in cfg.non_product_labels)


def mc_channel_name(cfg: Config, raw) -> str:
    """'Hylme Shop - shopee' -> 'Shopee' (index.html: mcChannelName)"""
    s = util.norm(raw)
    if not s:
        return ""
    for pattern, name in cfg.mc_channel_alias:
        if pattern.search(s):
            return name
    parts = re.split(r"\s+-\s+", s)
    return parts[-1] if parts else s


def mc_lead_intro(cfg: Config, raw) -> str:
    """สร้างข้อความ Lead Intro จากชื่อช่องทางของ MyCloud (index.html: mcLeadIntro)"""
    s = util.norm(raw)
    name = mc_channel_name(cfg, s)
    if name in ("Facebook", "Instagram"):
        parts = re.split(r"\s+-\s+", s)
        shop_part = " - ".join(parts[:-1]) if len(parts) > 1 else ""
        return f"{name} : {shop_part or name}"
    return name or cfg.channels["unknown_channel_label"]


# ---------------------------------------------------------------- คน / ทีม

def lead_page_key(lead_intro) -> str:
    """ออเดอร์ใบนี้มาจากเพจไหน — ตัวตั้งของ Conversion Rate (index.html: canonLeadPage)

    คืน 'none' เมื่อระบุเพจไม่ได้ (ออเดอร์กลุ่มนี้ไม่ถูกนับเป็นตัวตั้งของเพจใดเลย)
    สังเกตว่าเช็ค LINE จาก intro ดิบ ไม่ใช่จากผลของ parse_channel — ตามโค้ดเดิมทุกบรรทัด
    """
    intro = str(lead_intro or "")
    ch = parse_channel(intro)
    if re.search(r"instagram", ch, re.I):
        return "ig"
    if "ไขมัน" in ch:
        return "fb_kaimun"
    if "ผิว" in ch:
        return "fb_piw"
    if "นอน" in ch:
        return "fb_non"
    if re.search(r"facebook", ch, re.I):
        return "fb_hylme"
    if re.search(r"line|ไลน์", intro, re.I):
        return "line_priv" if re.search(r"privilege", intro, re.I) else "line_off"
    return "none"


def canon_person(cfg: Config, name) -> str:
    """ชื่อผู้ดูแลมาตรฐาน (index.html: canonPerson)"""
    k = util.norm(name)
    if not k:
        return "ไม่ระบุ"
    aliases = cfg.mappings.get("person_aliases") or {}
    return aliases.get(k.lower(), k)


def map_sales_person(cfg: Config, name) -> str:
    """ชื่อใน CRM -> ชื่อผู้ขายในระบบ MyCloud (index.html: mapSalesPerson)

    ไม่รู้จัก -> ใช้ชื่อเดิมไปก่อน
    """
    key = util.norm(name)
    return cfg.sales_person_map.get(key) or cfg.sales_person_map_ci.get(key.lower()) or key


def map_pancake_rep(cfg: Config, name) -> str:
    """ชื่อจาก Pancake -> ชื่อเต็ม (index.html: mapPancakeRep)"""
    k = util.norm(name)
    if not k:
        return ""
    return cfg.pancake_rep_map.get(k.lower(), k)


def team_of(cfg: Config, owner: str, lead_intro: str) -> str:
    """ออเดอร์นี้เป็นของทีมไหน (index.html: teamOfLead)

    ดูช่องทางก่อน (แม่นที่สุด) ถ้าไม่ได้ค่อยดูจากชื่อผู้ดูแล
    """
    by_ch = cfg.team_of_channel.get(channel_label(cfg, owner, lead_intro))
    if by_ch:
        return by_ch
    by_person = cfg.team_of_person.get(canon_person(cfg, owner))
    if by_person:
        return by_person
    return "other"      # Shopee / Tiktok / Lazada / ไม่ระบุช่องทาง


# ---------------------------------------------------------------- การชำระเงิน

PAYMENT_COD = "COD (เก็บเงินปลายทาง)"
PAYMENT_TRANSFER = "โอนเงิน"
PAYMENT_UNKNOWN = "ไม่ระบุ"
PAYMENT_MARKETPLACE = "Marketplace"


def payment_method(intro_text) -> str:
    """วิธีชำระเงินจากข้อความ Lead Intro (index.html: paymentMethod)

    'ปลายทาง' คือ COD อีกแบบหนึ่ง ต้องเช็คก่อน 'โอน'
    """
    s = str(intro_text or "")
    if re.search(r"COD|ปลายทาง", s, re.I):
        return PAYMENT_COD
    if "โอน" in s:
        return PAYMENT_TRANSFER
    return PAYMENT_UNKNOWN


def is_marketplace_order(cfg: Config, owner: str, source: str, lead_intro: str) -> bool:
    """index.html: isMarketplaceOrder — เช็ค 3 ทาง"""
    if util.norm(owner) == cfg.mappings["marketplace_rep"]:
        return True
    if re.search(r"marketplace|mycloud", str(source or ""), re.I):
        return True
    return bool(cfg.re_marketplace.search(str(lead_intro or "")))


def payment_method_of(cfg: Config, owner: str, source: str, lead_intro: str) -> str:
    """วิธีชำระเงินสำหรับแสดงผล/สรุป — marketplace ชนะทุกกรณี (index.html: paymentMethodOf)

    ไม่ใช้กับ export MC / POS ซึ่งยังต้องแยก COD vs โอน จริง ๆ
    """
    if is_marketplace_order(cfg, owner, source, lead_intro):
        return PAYMENT_MARKETPLACE
    return payment_method(lead_intro)


# ---------------------------------------------------------------- ที่อยู่

def resolve_province(province_field, address_text) -> tuple[str | None, str]:
    """คืน (จังหวัด, ที่มา) — ช่องจังหวัดชนะ ถ้าไม่มีค่อยอ่านจากที่อยู่ (index.html: resolveProvince)"""
    if province_field and str(province_field).strip():
        return str(province_field).strip(), "field"
    parsed = util.find_province_in_text(address_text or "")
    if parsed:
        return parsed, "parsed"
    return None, "unknown"


def is_metro(cfg: Config, province) -> bool:
    return bool(province) and province in cfg.metro_provinces


def delivery_mode(cfg: Config, is_cod: bool, province, province_source: str) -> str:
    """โหมดจัดส่งสำหรับไฟล์ MyCloud (index.html: buildMcRows)

    หาจังหวัดไม่พบ -> ถือเป็นต่างจังหวัด (EMS) ตามพฤติกรรมเดิม
    """
    metro = False if province_source == "unknown" else is_metro(cfg, province)
    modes = cfg.channels["delivery_modes"]
    if is_cod:
        return modes["cod_metro"] if metro else modes["cod_upcountry"]
    return modes["prepaid_metro"] if metro else modes["prepaid_upcountry"]


def build_address(parts: dict) -> str:
    """ประกอบที่อยู่จากฟิลด์ที่มี (index.html: buildAddress)

    เติมตำบล/อำเภอ/จังหวัดเฉพาะตอนที่บรรทัดที่อยู่ยังไม่ได้เขียนไว้แล้ว
    '-' ใน CRM แปลว่า 'ไม่ได้กรอก' ไม่ใช่รหัสไปรษณีย์จริง
    """
    out = []
    for key in ("address1", "address2"):
        v = parts.get(key)
        if v:
            out.append(str(v).strip())
    joined = " ".join(out)
    structured = []
    if parts.get("sub_district") and parts["sub_district"] not in joined:
        structured.append("ต." + str(parts["sub_district"]).strip())
    if parts.get("district") and parts["district"] not in joined:
        structured.append("อ." + str(parts["district"]).strip())
    if parts.get("province") and parts["province"] not in joined:
        structured.append("จ." + str(parts["province"]).strip())
    if structured:
        out.append(" ".join(structured))
    zip_ = str(parts.get("postcode") or "").strip()
    if zip_ and zip_ != "-" and zip_ not in joined:
        out.append(zip_)
    return re.sub(r"\s+", " ", " ".join(x for x in out if x)).strip()


def build_recipient_name(first, last, fallback_name) -> str:
    """ชื่อผู้รับ (index.html: buildRecipientName) — ชื่อ 'Deleted' ถือว่าไม่มี"""
    first = str(first or "").strip()
    last = str(last or "").strip()
    combined = " ".join(x for x in (first, last) if x)
    if combined and not re.search(r"deleted", combined, re.I):
        return combined
    lead_name = str(fallback_name or "").strip()
    return "" if re.search(r"deleted", lead_name, re.I) else lead_name


# ---------------------------------------------------------------- สถานะจัดส่ง

def delivery_status(cfg: Config, *raw_statuses) -> str | None:
    """แปลงสถานะจากไฟล์ต้นทางเป็นสถานะมาตรฐานของ fulfilment_events

    ไล่จากสถานะที่ 'จบแล้ว' ก่อน (cancelled/returned ชนะ delivered ชนะ in_transit)
    """
    mapping = cfg.statuses["delivery_status_map"]
    found = set()
    for raw in raw_statuses:
        s = util.norm(raw).lower()
        if not s or s == "n/a":
            continue
        for token, std in mapping.items():
            if token in s:
                found.add(std)
    for priority in ("cancelled", "returned", "refused", "lost", "delivered", "in_transit"):
        if priority in found:
            return priority
    return None
