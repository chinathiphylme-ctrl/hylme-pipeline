# -*- coding: utf-8 -*-
"""ฟังก์ชันพื้นฐาน — พอร์ตมาจาก index.html แบบบรรทัดต่อบรรทัด

ทุกฟังก์ชันในไฟล์นี้มีคู่ของมันอยู่ใน index.html และต้องให้ผลลัพธ์เหมือนกันเป๊ะ
ชื่อฟังก์ชันต้นทางเขียนกำกับไว้ในแต่ละ docstring เพื่อให้ตรวจย้อนกลับได้
"""
from __future__ import annotations

import datetime as _dt
import math
import re

# ---------------------------------------------------------------- ตัวเลข / เงิน

def num(v, default: float = 0.0) -> float:
    """แปลงค่าเป็นตัวเลข ตัดคอมมา ช่องว่าง และสัญลักษณ์บาทออก (index.html: pkNum / mcmNum)"""
    if v is None:
        return default
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = re.sub(r"[,\s฿฿]", "", str(v))
    if s in ("", "-"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def rv(n) -> float:
    """ปัดทศนิยม 2 ตำแหน่ง (index.html: const rv = n => Math.round(n*100)/100)"""
    return round(float(num(n)) * 100) / 100


def with_vat(n, vat_rate: float) -> float:
    """index.html: const withVat = n => n * (1 + VAT_RATE)"""
    return num(n) * (1 + vat_rate)


def ex_vat(n, vat_rate: float) -> float:
    """ถอด VAT ออกจากยอดที่รวม VAT แล้ว"""
    return num(n) / (1 + vat_rate)


def allocate_whole_baht(values) -> list[int]:
    """ปัดชุดตัวเลขให้เป็นจำนวนเต็ม โดยผลรวมยังเท่าเดิม (largest remainder method)

    index.html: allocateWholeBaht()
    เช่น [1344.99, 1345.01] -> [1345, 1345] ไม่ใช่ [1345, 1345.01] หรือ [1344, 1345]
    """
    values = list(values)
    if not values:
        return []
    floors = [math.floor(v) for v in values]
    target = round(sum(values))
    rem = target - sum(floors)
    by_frac = sorted(
        ({"i": i, "frac": v - math.floor(v)} for i, v in enumerate(values)),
        key=lambda x: x["frac"],
        reverse=True,
    )
    k = 0
    while k < len(by_frac) and rem > 0:
        floors[by_frac[k]["i"]] += 1
        rem -= 1
        k += 1
    if rem > 0:                      # กันเหนียว ไม่ควรเกิด
        floors[by_frac[0]["i"]] += rem
    return floors


def split_whole_unit_prices(line_total, qty) -> list[dict]:
    """แตกยอดรวมของบรรทัดออกเป็นราคาต่อหน่วยจำนวนเต็ม (index.html: splitWholeUnitPrices)

    หารลงตัว -> 1 ก้อน | หารไม่ลงตัว -> 2 ก้อน (ต่างกัน 1 บาท) ผลรวมตรงเป๊ะเสมอ
    splitWholeUnitPrices(1345, 3) -> [{price: 449, qty: 1}, {price: 448, qty: 2}]
    """
    q = round(num(qty))
    total = round(num(line_total))
    if q <= 0:
        return [{"price": total, "qty": q}]
    base = total // q
    rem = total - base * q
    if rem == 0:
        return [{"price": int(base), "qty": q}]
    return [{"price": int(base) + 1, "qty": rem}, {"price": int(base), "qty": q - rem}]


# ---------------------------------------------------------------- ข้อความ

def norm(v) -> str:
    """index.html: const mcNorm = v => String(v).replace(/\\s+/g,' ').trim()"""
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def is_na(v) -> bool:
    """MyCloud ใส่ 'N/A' แทนช่องว่าง — ต้องถือว่าว่าง (index.html: mcIsNA)"""
    t = norm(v)
    return (not t) or bool(re.fullmatch(r"n/?a", t, re.I))


def pick(*vals) -> str:
    """คืนค่าแรกที่ไม่ว่างและไม่ใช่ N/A (index.html: mcPick)"""
    for v in vals:
        if not is_na(v):
            return norm(v)
    return ""


def is_masked(v) -> bool:
    """ข้อมูลที่ถูกปิดบังตาม PDPA เช่น 'ช******ม' (index.html: isMaskedText)"""
    return "*" in str("" if v is None else v)


# ---------------------------------------------------------------- เบอร์โทร

def digits_only(v) -> str:
    return re.sub(r"[^\d]", "", str("" if v is None else v))


def norm_phone_digits(d) -> str:
    """66xxxxxxxxx / 0066xxxxxxxxx -> 0xxxxxxxxx (index.html: normPhoneDigits)"""
    d = digits_only(d)
    if re.fullmatch(r"0066\d{8,9}", d):
        d = "0" + d[4:]
    elif re.fullmatch(r"66\d{8,9}", d):
        d = "0" + d[2:]
    return d


def is_thai_mobile(d) -> bool:
    return bool(re.fullmatch(r"0[689]\d{8}", d or ""))


def is_thai_landline(d) -> bool:
    return bool(re.fullmatch(r"0[2-7]\d{7,8}", d or ""))


def is_thai_phone(d) -> bool:
    return is_thai_mobile(d) or is_thai_landline(d)


# ตัดรหัสประเทศ +66 / 0066 / 66 ออกก่อนหาเบอร์
#   loose  = กติกาเดิมจาก index.html บรรทัด 2240 ทุกประการ
#   strict = ตัดเฉพาะตอนที่ '66' อยู่ "หัวเบอร์" จริง ๆ (หน้ามันไม่ใช่ตัวเลข)
# ดูคำอธิบายว่าทำไมต้องมีสองแบบที่ฟังก์ชัน extract_thai_phone ข้างล่าง
_CC_LOOSE = re.compile(r"(?:\+|00)?66[\s\-]?(?=[689]\d)")
_CC_STRICT = re.compile(r"(?<![\d])(?:\+|00)?66[\s\-]?(?=[689]\d)")
_PHONE_CC_STRICT = False


def set_phone_country_code_mode(strict: bool) -> None:
    """เลือกกติกาตัดรหัสประเทศ — เรียกครั้งเดียวตอนอ่าน config"""
    global _PHONE_CC_STRICT
    _PHONE_CC_STRICT = bool(strict)


def extract_thai_phone(text) -> str:
    """ดึงเบอร์ไทยจากข้อความอิสระ โดยมีขอบเขตหน้า/หลัง (index.html: extractThaiPhone)

    กันเคสเดิมที่รหัสไปรษณีย์ติดมากับเบอร์ เช่น '...อยุธยา13000 0814347070'

    ข้อควรรู้ — จุดบกพร่องที่ติดมาจากของเดิม
    ------------------------------------------------------------------
    บรรทัดตัดรหัสประเทศของเดิมไม่ได้บังคับว่า '66' ต้องอยู่หน้าสุดของเบอร์
    เบอร์ที่มี '66' อยู่กลางเบอร์และตามด้วยเลข 6/8/9 จึงถูกตัดผิด เช่น
        0866666666  ->  '66' ตัวที่ 3-4 โดนแทนด้วย '0'  ->  เหลือ 7 หลัก -> หาเบอร์ไม่เจอ
    ผลคือออเดอร์นั้นกลายเป็น "ไม่มีเบอร์" จับคู่ลูกค้าซ้ำไม่ได้ และนับเป็นลูกค้าใหม่
    (ยอดขายไม่กระทบ เพราะเบอร์ไม่ได้ใช้คิดเงิน)

    กระทบเฉพาะเบอร์ที่ต้อง "แกะจากข้อความ" เช่น ช่อง 'ข้อมูลการจัดส่ง' ของ Google Sheet เก่า
    ถ้าไฟล์มีคอลัมน์เบอร์แยกอยู่แล้ว จะไม่ผ่านทางนี้

    ค่าเริ่มต้นคงกติกาเดิมไว้ (phone_country_code_strict = false) เพื่อให้ผลตรงกับ index.html
    ก่อน เปิด strict ใน config/rules.json เมื่อพร้อมรับตัวเลขลูกค้าที่จะเปลี่ยน
    """
    s = re.sub(r"[‐‑‒–—]", "-", str(text or ""))
    s = (_CC_STRICT if _PHONE_CC_STRICT else _CC_LOOSE).sub("0", s)
    out = [digits_only(m.group(2)) for m in re.finditer(r"(^|[^\d])(0[689](?:[ \-.]?\d){8})(?!\d)", s)]
    if out:
        return out[0]
    out = [digits_only(m.group(2)) for m in re.finditer(r"(^|[^\d])(0[2-7](?:[ \-.]?\d){7,8})(?!\d)", s)]
    return out[0] if out else ""


def clean_phone(raw_phone, address_text: str = "") -> str:
    """เบอร์ที่ใช้งานได้จริง: ฟิลด์ Phone -> กู้จากที่อยู่ -> ตัดจากตัวเลขที่ติดกันมา

    index.html: cleanPhone(rawPhone, account)
    """
    d = norm_phone_digits(raw_phone)
    if is_thai_phone(d):
        return d
    from_addr = extract_thai_phone(address_text or "")
    if is_thai_phone(from_addr):
        return from_addr
    inner = re.search(r"0[689]\d{8}", d)
    return inner.group(0) if inner else ""


def customer_key(v) -> str | None:
    """คีย์ลูกค้า = เบอร์โทรที่ normalize แล้ว (hylme_engine/02_load_orders.py: norm_phone)

    ใช้ตัวเดียวกับเครื่องคำนวณเดิมเพื่อให้ join กับ customers/customer_state ได้ตรง
    """
    if v is None:
        return None
    d = digits_only(v)
    if d.startswith("66") and len(d) in (11, 12):
        d = "0" + d[2:]
    if len(d) == 9 and not d.startswith("0"):
        d = "0" + d
    if len(d) in (9, 10) and d.startswith("0"):
        return d
    return None


def cx_phone(v) -> str:
    """เบอร์สำหรับจับคู่ยกเลิก — 9 หลักท้าย เบอร์ที่ถูกปิดบังถือว่าใช้ไม่ได้ (index.html: cxPhone)"""
    raw = str("" if v is None else v)
    if "*" in raw:
        return ""
    d = re.sub(r"\D", "", raw)
    return d[-9:] if len(d) >= 9 else ""


def cx_name(v) -> str:
    """ชื่อสำหรับจับคู่ยกเลิก — ตัดช่องว่างและแปลงเป็นตัวพิมพ์เล็ก (index.html: cxName)"""
    raw = str("" if v is None else v)
    if "*" in raw:
        return ""
    return re.sub(r"[\s​]", "", raw).lower()


# ---------------------------------------------------------------- วันที่

def _p2(n) -> str:
    return str(int(n)).zfill(2)


def pk_datetime(date_str, time_str) -> str | None:
    """'23/07/2026' + '23:08' -> '2026-07-23 23:08:00' (index.html: pkDateTime)

    รองรับปี พ.ศ. (มากกว่า 2500 -> ลบ 543)
    """
    if isinstance(date_str, (_dt.datetime, _dt.date)):
        d = date_str
        hh, mm = "00", "00"
        if isinstance(time_str, _dt.datetime):
            hh, mm = _p2(time_str.hour), _p2(time_str.minute)
        elif isinstance(d, _dt.datetime):
            hh, mm = _p2(d.hour), _p2(d.minute)
        return f"{d.year}-{_p2(d.month)}-{_p2(d.day)} {hh}:{mm}:00"

    ds = str(date_str or "").strip()
    ts = str(time_str or "").strip()
    dm = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", ds) or re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", ts)
    hh, mm = "00", "00"
    tm = re.search(r"(\d{1,2}):(\d{2})", ts) or re.search(r"(\d{1,2}):(\d{2})", ds)
    if tm:
        hh, mm = str(tm.group(1)).zfill(2), tm.group(2)
    if not dm:
        # เผื่อไฟล์ที่ openpyxl อ่านออกมาเป็น ISO อยู่แล้ว
        iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2}))?", ds)
        if iso:
            h = _p2(iso.group(4) or 0)
            m = iso.group(5) or "00"
            return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)} {h}:{m}:00"
        return None
    day, mon, year = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
    if year > 2500:
        year -= 543
    return f"{year}-{_p2(mon)}-{_p2(day)} {hh}:{mm}:00"


def mc_datetime(v) -> str:
    """'2026-07-24T10:33:37+07:00' -> '2026-07-24 10:33' (index.html: mcDateTime)

    ตัดตรง ๆ ไม่แปลง timezone เพราะ export เป็นเวลาไทยอยู่แล้ว
    """
    if isinstance(v, _dt.datetime):
        return f"{v.year}-{_p2(v.month)}-{_p2(v.day)} {_p2(v.hour)}:{_p2(v.minute)}"
    if isinstance(v, _dt.date):
        return f"{v.year}-{_p2(v.month)}-{_p2(v.day)} 00:00"
    s = norm(v)
    if not s or re.fullmatch(r"n/?a", s, re.I) or s == "-":
        return ""
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", s)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)} {iso.group(4)}:{iso.group(5)}"
    dmy = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ ,]+(\d{1,2}):(\d{2}))?", s)
    if dmy:
        return (f"{dmy.group(3)}-{_p2(dmy.group(2))}-{_p2(dmy.group(1))} "
                f"{_p2(dmy.group(4) or 0)}:{_p2(dmy.group(5) or 0)}")
    date_only = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if date_only:
        return f"{s} 00:00"
    return ""


def manual_cancel_date(v) -> str:
    """วันที่ในไฟล์ออเดอร์ยกเลิกของ Hylme (index.html: manualCancelDate)"""
    if isinstance(v, (_dt.datetime, _dt.date)):
        return f"{v.year}-{_p2(v.month)}-{_p2(v.day)}"
    if isinstance(v, (int, float)) and 20000 < v < 60000:      # Excel serial
        d = _dt.datetime(1899, 12, 30) + _dt.timedelta(days=round(v))
        return f"{d.year}-{_p2(d.month)}-{_p2(d.day)}"
    s = str("" if v is None else v).strip()
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m:
        yy = int(m.group(3))
        if yy > 2500:
            yy -= 543
        return f"{yy}-{_p2(m.group(2))}-{_p2(m.group(1))}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{_p2(m.group(2))}-{_p2(m.group(3))}"
    return ""


def biz_date_str(v, start_hour: int = 0) -> str | None:
    """รอบวันทำงาน (index.html: bizDateStr)

    start_hour = 0 -> เที่ยงคืนถึงเที่ยงคืน (ค่าปัจจุบัน)
    start_hour = 1 -> วันที่ N คือ 01:00 ของวันที่ N ถึง 00:59 ของวันถัดไป
    """
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        d = v
        if d.hour < start_hour:
            d = d - _dt.timedelta(days=1)
        return f"{d.year}-{_p2(d.month)}-{_p2(d.day)}"
    if isinstance(v, _dt.date):
        return f"{v.year}-{_p2(v.month)}-{_p2(v.day)}"
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2}))?", s)
    if m:
        if m.group(4) is None:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        d = _dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if int(m.group(4)) < start_hour:
            d -= _dt.timedelta(days=1)
        return f"{d.year}-{_p2(d.month)}-{_p2(d.day)}"
    return None


def shift_date_str(date_str: str, days: int) -> str:
    """เลื่อนวันที่ถอยหลัง N วัน (index.html: mcmShiftDateStr — ใช้ค่าบวก = ถอยหลัง)"""
    try:
        d = _dt.date.fromisoformat(str(date_str)[:10]) - _dt.timedelta(days=days)
        return d.isoformat()
    except (ValueError, TypeError):
        return ""


def today_str() -> str:
    return _dt.date.today().isoformat()


def now_str() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- ที่อยู่

THAI_PROVINCES = sorted([
    "กรุงเทพมหานคร", "กระบี่", "กาญจนบุรี", "กาฬสินธุ์", "กำแพงเพชร", "ขอนแก่น", "จันทบุรี",
    "ฉะเชิงเทรา", "ชลบุรี", "ชัยนาท", "ชัยภูมิ", "ชุมพร", "เชียงราย", "เชียงใหม่", "ตรัง", "ตราด",
    "ตาก", "นครนายก", "นครปฐม", "นครพนม", "นครราชสีมา", "นครศรีธรรมราช", "นครสวรรค์", "นนทบุรี",
    "นราธิวาส", "น่าน", "บึงกาฬ", "บุรีรัมย์", "ปทุมธานี", "ประจวบคีรีขันธ์", "ปราจีนบุรี", "ปัตตานี",
    "พระนครศรีอยุธยา", "พังงา", "พัทลุง", "พิจิตร", "พิษณุโลก", "เพชรบุรี", "เพชรบูรณ์", "แพร่",
    "ภูเก็ต", "มหาสารคาม", "มุกดาหาร", "แม่ฮ่องสอน", "ยโสธร", "ยะลา", "ร้อยเอ็ด", "ระนอง", "ระยอง",
    "ราชบุรี", "ลพบุรี", "ลำปาง", "ลำพูน", "เลย", "ศรีสะเกษ", "สกลนคร", "สงขลา", "สตูล",
    "สมุทรปราการ", "สมุทรสงคราม", "สมุทรสาคร", "สระแก้ว", "สระบุรี", "สิงห์บุรี", "สุโขทัย",
    "สุพรรณบุรี", "สุราษฎร์ธานี", "สุรินทร์", "หนองคาย", "หนองบัวลำภู", "อ่างทอง", "อำนาจเจริญ",
    "อุดรธานี", "อุตรดิตถ์", "อุทัยธานี", "อุบลราชธานี",
], key=len, reverse=True)
"""77 จังหวัด เรียงจากชื่อยาวไปสั้น เพื่อไม่ให้ชื่อสั้นบังชื่อยาวตอนค้นในข้อความ"""


def find_province_in_text(text) -> str | None:
    """index.html: findProvinceInText"""
    s = str(text or "")
    if re.search(r"กรุงเทพ|กทม", s):
        return "กรุงเทพมหานคร"
    for prov in THAI_PROVINCES:
        if prov in s:
            return prov
    return None


def find_zip_in_text(text) -> str | None:
    """index.html: findZipInText"""
    m = re.search(r"\b(\d{5})\b", str(text or ""))
    return m.group(1) if m else None
