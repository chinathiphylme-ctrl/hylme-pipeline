# -*- coding: utf-8 -*-
"""Conversion Rate — สร้าง data/output/dashboard/conversion.json

พอร์ตจาก index.html: collectPageStat() (บรรทัด 4289) + renderPageConversion() (บรรทัด 8180)

--------------------------------------------------------------------------
สูตร — มี 2 ตัวเลข ใช้ตัวตั้งคนละอย่าง ต้องทำให้ครบทั้งคู่
--------------------------------------------------------------------------
  หลัก (รายเพจ)   conv = closes ÷ new_cust
                  closes = จำนวนออเดอร์ในฐานข้อมูลที่ lead_page_key() แมปเข้าเพจนั้น
                  (index.html บรรทัด 8232)

  รายวัน / รวมหลายเพจ
                  conv = orders ÷ new_cust
                  orders = คอลัมน์ "ยอดออเดอร์" ในไฟล์สถิติหน้าเอง
                  (index.html: psConv บรรทัด 4328)

*** ไม่ใช้คอลัมน์ % ของ Pancake *** เป็น % รายวัน บวกกันไม่ได้ (ในชีต by_page ทะลุ 100%)

--------------------------------------------------------------------------
กติกาที่พลาดไม่ได้
--------------------------------------------------------------------------
ตัวตั้ง closes นับ "เฉพาะวันที่ไฟล์สถิติหน้ามีข้อมูลจริง" ไม่ใช่ทั้งช่วงที่เลือก
เพราะตัวหาร (ลูกค้าใหม่) มีแค่วันที่มีไฟล์ — ถ้าตัวตั้งเป็นทั้งเดือน Conversion จะพุ่งเกินจริง
(index.html บรรทัด 8219–8228)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .. import util
from ..config import Config
from ..loaders.pagestat import PS_KEYS, add, blank

SCHEMA_VERSION = 1


def _load_files(con: sqlite3.Connection) -> list[dict]:
    """อ่านไฟล์สถิติหน้าทั้งหมดจากฐานข้อมูล กลับเป็นรูปเดียวกับ DATA.pageStatFiles เดิม"""
    cols = ", ".join(PS_KEYS)
    files = []
    for f in con.execute(
        "SELECT file_sha, file_name, page_keys, pages_json, date_from, date_to, range_text "
        "FROM page_stat_file ORDER BY file_name"
    ).fetchall():
        sha = f["file_sha"]
        by_time = {
            r["stat_date"]: {k: util.num(r[k]) for k in PS_KEYS}
            for r in con.execute(
                f"SELECT stat_date, {cols} FROM page_stat_day WHERE file_sha=? ORDER BY stat_date",
                (sha,),
            )
        }
        by_page = {
            r["page_key"]: {k: util.num(r[k]) for k in PS_KEYS}
            for r in con.execute(
                f"SELECT page_key, {cols} FROM page_stat_page WHERE file_sha=?", (sha,)
            )
        }
        files.append({
            "file": f["file_name"],
            "pages": json.loads(f["pages_json"] or "[]"),
            "by_time": by_time,
            "by_page": by_page,
            "date_list": sorted(by_time.keys()),
            "from": f["date_from"],
            "to": f["date_to"],
            "range_text": f["range_text"] or "",
            "set_id": f["page_keys"] or "",
        })
    return files


def collect(files: list[dict], period_dates: set[str]) -> dict | None:
    """รวมสถิติเพจของช่วงที่เลือก (index.html: collectPageStat)

    กติกากันนับซ้ำ — ไล่ไฟล์ตามลำดับนี้
        1. ไฟล์ที่มีเพจน้อยกว่าก่อน (เพจเดียว = แยกรายเพจ x รายวันได้แม่นที่สุด)
        2. ไฟล์ที่ช่วงวันอยู่ในช่วงที่เลือกพอดี (contained) ก่อน
        3. ไฟล์ที่มีวันตรงกับช่วงมากกว่าก่อน
    ไฟล์ที่ครอบคลุม (วัน × เพจ) ซ้ำกับไฟล์ที่รับไปแล้ว จะถูกข้ามทั้งไฟล์
    """
    if not files:
        return None
    D = set(period_dates or [])

    ranked = []
    for f in files:
        in_dates = [d for d in f["date_list"] if d in D]
        if not in_dates:
            continue
        contained = bool(f["date_list"]) and all(d in D for d in f["date_list"])
        ranked.append({"f": f, "in_dates": in_dates, "contained": contained})
    ranked.sort(key=lambda x: (len(x["f"]["pages"]), 0 if x["contained"] else 1, -len(x["in_dates"])))

    used: set[str] = set()
    per_page: dict[str, dict] = {}
    daily: dict[str, dict] = {}
    skipped: list[str] = []
    combo = None

    for x in ranked:
        f = x["f"]
        keys = [p["key"] for p in f["pages"]] or ["fb_hylme"]
        if any(f"{d}|{k}" in used for d in x["in_dates"] for k in keys):
            skipped.append(f["file"])
            continue
        for d in x["in_dates"]:
            for k in keys:
                used.add(f"{d}|{k}")
        for d in x["in_dates"]:
            m = f["by_time"].get(d)
            if m:
                add(daily.setdefault(d, blank()), m)

        if x["contained"] and f["by_page"]:
            for k, m in f["by_page"].items():
                add(per_page.setdefault(k, blank()), m)
        elif len(keys) == 1:
            for d in x["in_dates"]:
                m = f["by_time"].get(d)
                if m:
                    add(per_page.setdefault(keys[0], blank()), m)
        else:
            combo = combo or {"keys": set(), "m": blank(), "files": []}
            combo["keys"].update(keys)
            for d in x["in_dates"]:
                m = f["by_time"].get(d)
                if m:
                    add(combo["m"], m)
            if f["by_page"]:
                combo["files"].append({"file": f["file"], "from": f["from"],
                                       "to": f["to"], "by_page": f["by_page"]})

    dates = sorted(daily.keys())
    if not dates:
        return {"empty": True, "files": len(files)}

    total = blank()
    for d in dates:
        add(total, daily[d])
    return {
        "total": total, "per_page": per_page, "daily": daily, "dates": dates,
        "skipped": skipped,
        "combo": ({"keys": sorted(combo["keys"]), "m": combo["m"], "files": combo["files"]}
                  if combo else None),
        "coverage": f"{len(dates)}/{len(period_dates or [])}",
    }


def _closes_by_date_page(con: sqlite3.Connection, cfg: Config) -> dict[str, dict[str, int]]:
    """ปิดการขายแยกตาม วัน × เพจ (index.html: closesByDatePage บรรทัด 6913)

    ออเดอร์ที่ระบุเพจไม่ได้ (page_key = 'none') ไม่ถูกนับเป็นตัวตั้งของเพจใดเลย — ตามโค้ดเดิม
    ออเดอร์เก่าที่โหลดเข้าฐานก่อนมี pipeline ไม่มี page_key จึงอนุมานจากคอลัมน์ channel
    (แยก LINE Privilege / Official ไม่ได้ -> ตกไปที่ line_off) ดูหมายเหตุใน conversion.json
    """
    valid = {p["key"] for p in cfg.channels["page_defs"]}
    channel_fallback = {
        "Facebook Hylme": "fb_hylme",
        "Facebook Hylme ครบจบเรื่องไขมัน": "fb_kaimun",
        "Facebook Hylme ครบจบเรื่องนอน": "fb_non",
        "Facebook Hylme ครบจบเรื่องผิว": "fb_piw",
        "Instagram": "ig",
        cfg.channels["crm_channel_label"]: "line_off",
    }
    out: dict[str, dict[str, int]] = {}
    n_fallback = 0
    for r in con.execute(
        "SELECT order_date, page_key, channel FROM orders "
        "WHERE order_date IS NOT NULL AND COALESCE(is_cancelled,0) = 0"
    ):
        k = r["page_key"]
        if not k:
            k = channel_fallback.get(r["channel"] or "")
            if k:
                n_fallback += 1
        if not k or k not in valid:
            continue
        day = out.setdefault(r["order_date"], {})
        day[k] = day.get(k, 0) + 1
    out["__fallback_count__"] = n_fallback     # ถอดออกก่อนเขียนไฟล์
    return out


def build(con: sqlite3.Connection, cfg: Config, out_dir: Path) -> dict:
    """เขียน conversion.json"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = _load_files(con)
    cbdp = _closes_by_date_page(con, cfg)
    n_fallback = cbdp.pop("__fallback_count__", 0)

    page_defs = cfg.channels["page_defs"]
    order_idx = {p["key"]: i for i, p in enumerate(page_defs)}
    name_of = {p["key"]: p for p in page_defs}

    def summarize(period_dates: list[str], label: str) -> dict | None:
        ps = collect(files, set(period_dates))
        if not ps or ps.get("empty"):
            return None
        stat_dates = ps["dates"]
        closes: dict[str, int] = {p["key"]: 0 for p in page_defs}
        for d in stat_dates:
            for k, n in (cbdp.get(d) or {}).items():
                if k in closes:
                    closes[k] += n
        # คอลัมน์ให้ตรงกับตารางเดิมใน index.html: ลูกค้าใหม่ · แชทใหม่ · เบอร์โทรใหม่ · ยอดออเดอร์ · conv · ปิดการขาย
        rows = []
        for k, m in ps["per_page"].items():
            d = name_of.get(k, {"page": k, "platform": "", "color": ""})
            c = closes.get(k, 0)
            rows.append({
                "key": k, "page": d["page"], "platform": d["platform"], "color": d["color"],
                "new_cust": m["new_cust"], "chat_new": m["chat_new"], "phone_new": m["phone_new"],
                "orders_file": m["orders"], "closes": c,
                "conv": round(c / m["new_cust"], 6) if m["new_cust"] else None,
            })
        rows.sort(key=lambda r: order_idx.get(r["key"], len(page_defs)))

        total = ps["total"]
        # index.html บรรทัด 8251: totalCloses รวมเฉพาะแถวรายเพจ ไม่รวมก้อน "รวมหลายเพจ"
        # ส่วนตัวหารเป็น ps.total ซึ่งรวมทุกไฟล์ — เป็นความไม่สมมาตรที่มีอยู่ในของเดิม ยกมาตามนั้น
        total_closes = sum(closes.get(r["key"], 0) for r in rows)
        return {
            "label": label,
            "stat_dates": stat_dates,
            "coverage": ps["coverage"],
            "skipped_files": ps["skipped"],
            "pages": rows,
            # ก้อน "รวมหลายเพจ" — แยกรายเพจ x รายวันไม่ได้ จึงใช้ psConv (ยอดออเดอร์ในไฟล์)
            # และไม่มีตัวเลขปิดการขาย ตรงกับของเดิมที่แสดงเป็น "—"
            "combo": ({"keys": ps["combo"]["keys"],
                       "new_cust": ps["combo"]["m"]["new_cust"],
                       "chat_new": ps["combo"]["m"]["chat_new"],
                       "phone_new": ps["combo"]["m"]["phone_new"],
                       "orders_file": ps["combo"]["m"]["orders"],
                       "closes": None,
                       "conv": (round(ps["combo"]["m"]["orders"] / ps["combo"]["m"]["new_cust"], 6)
                                if ps["combo"]["m"]["new_cust"] else None),
                       "by_page_share": ps["combo"]["files"]}
                      if ps["combo"] else None),
            "total": {
                "new_cust": total["new_cust"],
                "orders_file": total["orders"],
                "closes": total_closes,
                "conv_closes": round(total_closes / total["new_cust"], 6) if total["new_cust"] else None,
                "conv_file": round(total["orders"] / total["new_cust"], 6) if total["new_cust"] else None,
            },
            # ตารางรายวันของเดิมมี 5 คอลัมน์: ลูกค้าใหม่ · แชทใหม่ · เบอร์โทรใหม่ · ยอดออเดอร์ · conv
            # และใช้ psConv (ยอดออเดอร์ในไฟล์ ÷ ลูกค้าใหม่) — ไม่มีคอลัมน์ "ปิดการขาย" รายวัน
            # จึงไม่ใส่เพิ่ม เพื่อไม่ให้มีตัวเลขที่ระบบเดิมไม่มี
            "daily": [
                {"date": d,
                 "new_cust": ps["daily"][d]["new_cust"],
                 "chat_new": ps["daily"][d]["chat_new"],
                 "phone_new": ps["daily"][d]["phone_new"],
                 "orders_file": ps["daily"][d]["orders"],
                 "conv_file": (round(ps["daily"][d]["orders"] / ps["daily"][d]["new_cust"], 6)
                               if ps["daily"][d]["new_cust"] else None)}
                for d in stat_dates
            ],
        }

    # ---- คำนวณล่วงหน้าให้ทุกเดือนที่มีข้อมูล ----
    all_dates = sorted({d for f in files for d in f["date_list"]})
    months: dict[str, list[str]] = {}
    for d in all_dates:
        months.setdefault(d[:7], []).append(d)
    monthly = [s for s in (summarize(ds, m) for m, ds in sorted(months.items())) if s]
    for s, m in zip(monthly, sorted(months.keys())):
        s["month"] = m

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": util.now_str(),
        "formula": {
            "primary": "closes / new_cust  (ปิดการขายจากไฟล์ออเดอร์ ÷ ลูกค้าใหม่จากไฟล์สถิติหน้า)",
            "file_based": "orders_file / new_cust  (ยอดออเดอร์ในไฟล์สถิติหน้า ÷ ลูกค้าใหม่)",
            "note": "ไม่ใช้คอลัมน์ % ของ Pancake — เป็น % รายวัน บวกกันไม่ได้ (ในชีต by_page ทะลุ 100%)",
            "closes_scope": "closes นับเฉพาะวันที่ไฟล์สถิติหน้ามีข้อมูลจริง (stat_dates) ไม่ใช่ทั้งช่วง",
        },
        "page_defs": page_defs,
        "team_page_keys": cfg.channels["team_page_keys"],
        "n_files": len(files),
        "files": [
            {"file": f["file"], "pages": f["pages"], "set_id": f["set_id"],
             "from": f["from"], "to": f["to"], "n_days": len(f["date_list"]),
             "range_text": f["range_text"],
             "by_time": {d: {"new_cust": m["new_cust"], "orders": m["orders"]}
                         for d, m in sorted(f["by_time"].items())},
             "by_page": {k: {"new_cust": m["new_cust"], "orders": m["orders"]}
                         for k, m in sorted(f["by_page"].items())}}
            for f in files
        ],
        "closes_by_date_page": {d: v for d, v in sorted(cbdp.items())},
        "monthly": monthly,
        "legacy_page_key_from_channel": n_fallback,
        "legacy_note": ("ออเดอร์ที่ไม่มี page_key (โหลดเข้าฐานก่อนมี pipeline) อนุมานเพจจากคอลัมน์ channel "
                        "— แยก LINE Privilege / Official ไม่ได้ จึงตกไปที่ line_off ทั้งหมด"),
    }

    path = out_dir / "conversion.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "files_loaded": len(files),
        "months": len(monthly),
        "days": len(all_dates),
        "legacy_fallback": n_fallback,
    }
