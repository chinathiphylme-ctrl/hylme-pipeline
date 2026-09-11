# -*- coding: utf-8 -*-
"""อ่านไฟล์ pages_statistics_page_*.xlsx (Pancake > สถิติ > สถิติหน้า)

พอร์ตจาก index.html บรรทัด 4174–4327:
    PS_COLS · psDate() · psPageKey() · psHeader() · isPageStatWorkbook() · parsePageStatWorkbook()

ไฟล์นี้เป็นตัวหารของ Conversion Rate — คอลัมน์ "ลูกค้าใหม่"
ไฟล์หนึ่งไฟล์มี 2 ชีต:
    *_by_time : 1 แถว = 1 วัน  (ยอดรวมของทุกเพจที่อยู่ในไฟล์)
    *_by_page : 1 แถว = 1 เพจ (ยอดรวมของทุกวันในช่วงที่โหลด)

*** ไม่ใช้คอลัมน์ % ของ Pancake ***
เพราะเป็น % รายวัน เอามาบวกกันไม่ได้ — ในชีต by_page ตัวเลขของ Pancake ทะลุ 100%
(เห็นเป็น 321% / 385%) Conversion จึงคำนวณเองจาก "ปิดการขาย ÷ ลูกค้าใหม่" เสมอ

เก็บข้อมูล "ระดับไฟล์" ไว้ทั้งก้อน ไม่รวบยอดตั้งแต่ตอนโหลด
เพราะกติกากันนับซ้ำใน collectPageStat() ขึ้นกับช่วงวันที่ผู้ใช้เลือก จึงต้องรวบตอนใช้งาน
"""
from __future__ import annotations

import re
from pathlib import Path

from . import LoadResult, read_sheet_matrix, sheet_names
from .. import util
from ..config import Config

# index.html: PS_COLS (บรรทัด 4181) — ลำดับและหัวคอลัมน์ต้องตรงกันเป๊ะ
PS_COLS = [
    ("new_cust",  "ลูกค้าใหม่"),
    ("phone_all", "เบอร์โทรศัพท์ทั้งหมด"),
    ("phone_new", "เบอร์โทรใหม่"),
    ("cmt_cust",  "คอมเม้นจากลูกค้าทั้งหมด"),
    ("chat_cust", "แชทจากลูกค้าทั้งหมด"),
    ("cmt_page",  "ความคิดเห็นจากเพจทั้งหมด"),
    ("chat_page", "แชทจากเพจทั้งหมด"),
    ("chat_new",  "แชทใหม่"),
    ("chat_old",  "แชทจากลูกค้าเก่า"),
    ("orders",    "ยอดออเดอร์"),
]
PS_KEYS = [k for k, _ in PS_COLS]

HEADER_FIRST_CELL = ("เวลา", "Page", "หน้า")
TITLE_MARKER = "สถิติหน้า"


def blank() -> dict:
    """index.html: psBlank()"""
    return {k: 0 for k in PS_KEYS}


def add(dst: dict, src: dict) -> dict:
    """index.html: psAdd()"""
    for k in PS_KEYS:
        dst[k] = dst.get(k, 0) + (util.num(src.get(k)) or 0)
    return dst


def _ps_date(v) -> str:
    """'4/8/2026' (d/m/yyyy) หรือ Date -> '2026-08-04' (index.html: psDate)"""
    import datetime as _dt
    if isinstance(v, (_dt.datetime, _dt.date)):
        return f"{v.year}-{str(v.month).zfill(2)}-{str(v.day).zfill(2)}"
    s = util.norm(v)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        y = int(m.group(3))
        if y > 2500:
            y -= 543
        return f"{y}-{str(int(m.group(2))).zfill(2)}-{str(int(m.group(1))).zfill(2)}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{str(int(m.group(2))).zfill(2)}-{str(int(m.group(3))).zfill(2)}"
    return ""


def page_key(name) -> str:
    """ชื่อเพจ -> key มาตรฐานชุดเดียวกับ PAGE_DEFS (index.html: psPageKey)"""
    n = str(name or "")
    if re.search(r"instagram|(^|[^a-z])ig([^a-z]|$)", n, re.I):
        return "ig"
    if "ไขมัน" in n:
        return "fb_kaimun"
    if "ผิว" in n:
        return "fb_piw"
    if "นอน" in n:
        return "fb_non"
    if re.search(r"line|ไลน์", n, re.I):
        return "line_priv" if re.search(r"privilege", n, re.I) else "line_off"
    return "fb_hylme"


def _find_header(aoa: list[list]) -> dict | None:
    """หาแถวหัวตาราง + ตำแหน่งคอลัมน์ (index.html: psHeader)

    คืน {idx, col, kind} — kind = 'time' (ชีตรายวัน) หรือ 'page' (ชีตรายเพจ)
    ต้องเจอทั้ง 'ลูกค้าใหม่' และ 'ยอดออเดอร์' ถึงจะนับว่าเป็นหัวตารางจริง
    """
    for i in range(min(len(aoa), 12)):
        row = [util.norm(x) for x in (aoa[i] or [])]
        if not row:
            continue
        c0 = row[0]
        if c0 not in HEADER_FIRST_CELL:
            continue
        col = {}
        for k, h in PS_COLS:
            col[k] = row.index(h) if h in row else -1
        if col["new_cust"] >= 0 and col["orders"] >= 0:
            return {"idx": i, "col": col, "kind": "time" if c0 == "เวลา" else "page"}
    return None


def _is_stat_sheet(aoa: list[list]) -> bool:
    """A1 ต้องมีคำว่า 'สถิติหน้า' (index.html: isPageStatWorkbook)"""
    first = util.norm((aoa[0] or [None])[0]) if aoa else ""
    return TITLE_MARKER in first


def detect(path: Path) -> str | None:
    """คืนชื่อชีตแรกที่เป็นสถิติหน้า หรือ None"""
    for sn in sheet_names(path):
        aoa = read_sheet_matrix(path, sn, max_rows=14)
        if _is_stat_sheet(aoa) and _find_header(aoa):
            return sn
    return None


def load(path: Path, cfg: Config, sheet: str | None = None) -> LoadResult:
    """อ่านทุกชีตของไฟล์ คืนโครงสร้างระดับไฟล์ (index.html: parsePageStatWorkbook)"""
    path = Path(path)
    st = {
        "file": path.name,
        "pages": [],          # [{id, name, key}]
        "by_time": {},        # {date: metrics}
        "by_page": {},        # {page_key: metrics}
        "date_list": [],
        "from": None,
        "to": None,
        "range_text": "",
        "set_id": "",
    }
    pages_line = ""
    range_line = ""

    for sn in sheet_names(path):
        aoa = read_sheet_matrix(path, sn)
        if not _is_stat_sheet(aoa):
            continue
        # บรรทัด 2-6 มี "หน้า: ..." และ "ช่วงเวลาดาวน์โหลด: ..."
        for i in range(1, min(len(aoa), 6)):
            t = util.norm((aoa[i] or [None])[0])
            if t.startswith("หน้า:"):
                pages_line = t[5:].strip()
            if t.startswith("ช่วงเวลาดาวน์โหลด:"):
                range_line = ":".join(t.split(":")[1:]).strip()

        h = _find_header(aoa)
        if not h:
            continue
        for i in range(h["idx"] + 1, len(aoa)):
            row = aoa[i] or []
            label = util.norm(row[0] if row else None)
            if not label:
                continue
            m = blank()
            for k in PS_KEYS:
                ci = h["col"][k]
                if ci >= 0 and ci < len(row):
                    m[k] = util.num(row[ci])
            if h["kind"] == "time":
                d = _ps_date(row[0])
                if not d:
                    continue
                add(st["by_time"].setdefault(d, blank()), m)
            else:
                # "923678677500634_Hylme ครบจบเรื่องนอน" -> id + ชื่อเพจ
                mm = re.match(r"^(\d+)_(.*)$", label)
                pid = mm.group(1) if mm else ""
                nm = mm.group(2) if mm else label
                key = page_key(nm)
                add(st["by_page"].setdefault(key, blank()), m)
                if not any(p["key"] == key for p in st["pages"]):
                    st["pages"].append({"id": pid, "name": nm, "key": key})

    # ไม่มีชีต by_page -> อ่านรายชื่อเพจจากบรรทัด "หน้า: ชื่อ (id), ชื่อ (id)"
    if not st["pages"] and pages_line:
        for part in re.split(r",\s*(?=[^)]*(?:\(|$))", pages_line):
            mm = re.match(r"^(.*?)\s*\((\d+)\)\s*$", part)
            nm = (mm.group(1) if mm else part).strip()
            if not nm:
                continue
            key = page_key(nm)
            if not any(p["key"] == key for p in st["pages"]):
                st["pages"].append({"id": mm.group(2) if mm else "", "name": nm, "key": key})

    st["date_list"] = sorted(st["by_time"].keys())
    st["from"] = st["date_list"][0] if st["date_list"] else None
    st["to"] = st["date_list"][-1] if st["date_list"] else None
    st["range_text"] = range_line
    st["set_id"] = "+".join(sorted(p["key"] for p in st["pages"]))

    res = LoadResult(kind="pagestat", file_name=path.name, sheet=sheet or "")
    res.rows_read = len(st["by_time"]) + len(st["by_page"])
    res.page_stat = st
    res.stats = {
        "pages": len(st["pages"]),
        "days": len(st["date_list"]),
        "from": st["from"],
        "to": st["to"],
        "page_keys": [p["key"] for p in st["pages"]],
        "new_cust": sum(m["new_cust"] for m in st["by_time"].values()),
        "orders": sum(m["orders"] for m in st["by_time"].values()),
    }
    if not st["date_list"] and not st["by_page"]:
        raise ValueError(f"{path.name}: อ่านไฟล์สถิติหน้าไม่ได้ — ไม่พบทั้งชีตรายวันและชีตรายเพจ")
    return res
