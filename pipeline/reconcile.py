# -*- coding: utf-8 -*-
"""จับคู่ทะเบียนออเดอร์ยกเลิกกับออเดอร์จริง

พอร์ตจาก index.html: applyCancelRegistry() — บันไดจับคู่ 6 ชั้น ไล่จากตัวชี้เฉพาะที่สุดไปหากว้างที่สุด

    L1  Shop Order No = order_id ตรง ๆ (ทีมคีย์เลข Lead ลงช่องนี้)
    L2  MKP- + เลขออเดอร์ของแพลตฟอร์ม
    L3  เลขออเดอร์ของ MyCloud (orders.mc_order_no)
    L4  เบอร์โทรผู้รับ + ยอดเงิน      <- แม่นสุดสำหรับออเดอร์ที่คีย์เอง
    L5  ชื่อผู้รับ + ยอดเงิน
    L6  วันที่ + ยอดเงิน (ต้องเหลือผู้สมัครรายเดียว และช่องทางต้องไม่ขัดกัน)

ทุกชั้นกันไม่ให้ออเดอร์ใบเดียวถูกหักซ้ำ

--------------------------------------------------------------------------
สิ่งที่เพิ่มจากของเดิม: ผลการจับคู่ถูกบันทึกลงตาราง cancellations
--------------------------------------------------------------------------
ของเดิมคำนวณใหม่ทั้งหมดทุกครั้งที่ recompute ทำให้เคสที่เคยจับคู่ได้แบบชัดเจน
กลายเป็น ambiguous ได้ในวันถัดมา เมื่อมีออเดอร์ใหม่ที่วันที่+ยอดเงินตรงกันเข้ามา
ที่นี่ ผลการจับคู่ที่เคยได้แล้วจะถูกใช้ต่อ ไม่ถูกคำนวณใหม่ (ยกเว้นสั่ง --recheck)
"""
from __future__ import annotations

import re
import sqlite3

from . import util
from .config import Config

MATCH_L1 = "เลข Lead ตรง"
MATCH_L2 = "เลขออเดอร์แพลตฟอร์ม"
MATCH_L3 = "เลขออเดอร์ MyCloud"
MATCH_L4 = "เบอร์โทร+ยอดเงิน"
MATCH_L5 = "ชื่อผู้รับ+ยอดเงิน"
MATCH_L6 = "วันที่+ยอดเงิน"


def _chan_token(text) -> str | None:
    """ดึงช่องทางเป็นคำสั้น ๆ ใช้กันจับคู่ผิดข้ามช่องทาง (index.html: cancelChanToken)"""
    t = str(text or "").lower()
    if re.search(r"shopee|ช้อปปี้", t):
        return "shopee"
    if re.search(r"lazada|ลาซาด้า", t):
        return "lazada"
    if re.search(r"tiktok|tik\s*tok", t):
        return "tiktok"
    if re.search(r"\bline\b|ไลน์", t):
        return "line"
    return None


class _Index:
    """ดัชนีของออเดอร์ทั้งหมด สำหรับจับคู่ (index.html: cancelLeadIndexes)"""

    def __init__(self, con: sqlite3.Connection, cfg: Config):
        self.by_id: dict[str, dict] = {}
        self.by_mc_no: dict[str, dict] = {}
        self.by_phone: dict[str, list[dict]] = {}
        self.by_name: dict[str, list[dict]] = {}
        self.by_amt: dict[str, list[dict]] = {}
        self.by_source: dict[tuple[str, str], str] = {}

        rows = con.execute(
            "SELECT order_id, order_date, amount_inc_vat, mc_order_no, phone_clean, "
            "       name_raw, channel, lead_intro, source, owner "
            "FROM orders"
        ).fetchall()
        for r in rows:
            o = dict(r)
            oid = str(o["order_id"])
            self.by_id[oid] = o
            if o["mc_order_no"]:
                self.by_mc_no[str(o["mc_order_no"])] = o
            ph = util.cx_phone(o["phone_clean"])
            o["_cx_phone"] = ph
            if ph:
                self.by_phone.setdefault(ph, []).append(o)
            nm = util.cx_name(o["name_raw"])
            if len(nm) >= 4:
                self.by_name.setdefault(nm, []).append(o)
            gross = round(util.num(o["amount_inc_vat"]))
            if o["order_date"]:
                self.by_amt.setdefault(f"{o['order_date']}|{gross}", []).append(o)
            o["_chan_token"] = _chan_token(
                " ".join(str(o.get(k) or "") for k in ("lead_intro", "source", "channel", "owner"))
            )

        for r in con.execute("SELECT system, external_id, order_id FROM order_sources"):
            self.by_source[(r["system"], str(r["external_id"]))] = r["order_id"]


def _narrow(candidates: list[dict], rec: dict, taken: set[str]) -> list[dict]:
    """เลือกจากผู้สมัครหลายราย: ยอดต้องตรง แล้วค่อยตัดด้วยวันที่ (index.html: narrow)"""
    target = round(util.num(rec["cancel_gross"]))
    c = [x for x in (candidates or [])
         if x["order_id"] not in taken and round(util.num(x["amount_inc_vat"])) == target]
    if len(c) <= 1:
        return c
    same_day = [x for x in c if x["order_date"] == rec["cancel_date"]]
    if len(same_day) == 1:
        return same_day
    # ออเดอร์ที่คีย์เข้า MC ล่วงหน้า 1 วัน -> ลองวันก่อนหน้าด้วย
    prev_day = [x for x in c if x["order_date"] == util.shift_date_str(rec["cancel_date"], 1)]
    return prev_day if len(prev_day) == 1 else c


# ต้นทางที่ "มีเลขออเดอร์ของแพลตฟอร์มติดมาเสมอ" — ไฟล์ marketplace / MyCloud
# ใบยกเลิกจากไฟล์พวกนี้ถ้าหาเลขออเดอร์ไม่เจอ แปลว่าออเดอร์นั้นไม่เคยถูกนับเป็นยอดขาย
# (loader ข้ามออเดอร์ที่สถานะยกเลิกตั้งแต่ตอนอ่านไฟล์) จึงไม่มีอะไรให้หัก — ห้ามเดาต่อ
STRONG_KEY_SOURCES = {"mycloud"}


def _match_one(rec: dict, idx: _Index, taken: set[str],
               cfg: Config) -> tuple[str | None, str, str]:
    """คืน (order_id, วิธีจับคู่, สถานะ)

    สถานะ
        matched          จับคู่ได้
        ambiguous        มีผู้สมัครหลายราย ต้องเลือกเอง
        no_source_order  มีเลขออเดอร์ชัดเจนแต่ไม่มีออเดอร์นั้นในฐาน = ไม่เคยถูกนับเป็นยอดขาย
        unmatched        หาไม่เจอและไม่มีเลขให้ยึด ต้องตรวจเอง
    """
    free = lambda oid: bool(oid) and oid not in taken

    # ---- L1 Shop Order No = order_id ตรง ๆ ----
    shop_no = str(rec.get("shop_no") or "").strip()
    if shop_no:
        for candidate in (shop_no, shop_no.upper()):
            if candidate in idx.by_id and free(candidate):
                return candidate, MATCH_L1, "matched"
        via_source = idx.by_source.get(("shop_order_no", shop_no))
        if free(via_source):
            return via_source, MATCH_L1, "matched"

    # ---- L2 เลขออเดอร์ของแพลตฟอร์ม ----
    for ext in (rec.get("chan_no"), rec.get("cancel_key")):
        if not ext:
            continue
        mkp_id = "MKP-" + str(ext)
        if mkp_id in idx.by_id and free(mkp_id):
            return mkp_id, MATCH_L2, "matched"
        for system in ("shopee", "lazada", "tiktok"):
            via = idx.by_source.get((system, str(ext)))
            if free(via):
                return via, MATCH_L2, "matched"

    # ---- L3 เลขออเดอร์ของ MyCloud ----
    mc_no = str(rec.get("mc_no") or "").strip()
    if mc_no:
        o = idx.by_mc_no.get(mc_no)
        if o and free(o["order_id"]):
            return o["order_id"], MATCH_L3, "matched"
        via = idx.by_source.get(("mycloud", mc_no))
        if free(via):
            return via, MATCH_L3, "matched"

    # ---- ด่านกันการเดา ----
    # ใบยกเลิกจากไฟล์ marketplace/MyCloud มีเลขออเดอร์ของแพลตฟอร์มติดมาเสมอ
    # ถ้า L1-L3 หาไม่เจอ แปลว่าออเดอร์นั้น "ไม่อยู่ในฐาน" เพราะถูกข้ามตอนโหลด (สถานะยกเลิก)
    # ไม่ใช่ "หาไม่เจอเพราะข้อมูลไม่พอ" — ไล่ลงไปเดาด้วยเบอร์/ชื่อ/ยอดเงินต่อจะจับผิดใบ
    # เคสจริงที่เจอ: 260910NPTQT1GD (Shopee) ถูกจับคู่กับ MKP-260910KNQ0V26G คนละใบกัน
    # เพราะบังเอิญวันเดียวกันยอด 491.00 เท่ากัน -> ออเดอร์ที่ขายได้จริงถูกหักทิ้ง
    if str(rec.get("source") or "") in STRONG_KEY_SOURCES and (shop_no or rec.get("chan_no") or mc_no):
        return None, "", "no_source_order"

    # ---- L4 เบอร์โทร + ยอดเงิน ----
    if rec.get("phone"):
        c = _narrow(idx.by_phone.get(rec["phone"]), rec, taken)
        if len(c) == 1:
            return c[0]["order_id"], MATCH_L4, "matched"

    # ---- L5 ชื่อผู้รับ + ยอดเงิน ----
    if rec.get("name") and len(rec["name"]) >= 4:
        c = _narrow(idx.by_name.get(rec["name"]), rec, taken)
        if len(c) == 1:
            return c[0]["order_id"], MATCH_L5, "matched"

    # ---- L6 วันที่ + ยอดเงิน (ช่องทางต้องไม่ขัดกัน) ----
    if cfg.rules.get("cancel_amount_match", True) and rec.get("cancel_date") and rec.get("cancel_gross"):
        rec_tok = _chan_token(rec.get("channel"))
        key = f"{rec['cancel_date']}|{round(util.num(rec['cancel_gross']))}"
        c = [x for x in idx.by_amt.get(key, []) if x["order_id"] not in taken]
        if rec_tok:
            # ออเดอร์ยกเลิกของ Shopee ต้องไม่ไปหักออเดอร์ Lazada
            c = [x for x in c if (not x["_chan_token"]) or x["_chan_token"] == rec_tok]
        if len(c) == 1:
            return c[0]["order_id"], MATCH_L6, "matched"
        if len(c) > 1:
            return None, "", "ambiguous"

    return None, "", "unmatched"


def upsert_cancellations(con: sqlite3.Connection, cancels: list[dict]) -> tuple[int, int]:
    """เขียนทะเบียนยกเลิกลงฐานข้อมูล คืน (ใหม่, อัปเดต)

    ไม่แตะคอลัมน์ผลการจับคู่ของแถวที่มีอยู่แล้ว — การจับคู่ที่เคยทำไว้ต้องคงอยู่
    """
    new = updated = 0
    now = util.now_str()
    for c in cancels:
        exists = con.execute(
            "SELECT 1 FROM cancellations WHERE cancel_key=?", (c["cancel_key"],)
        ).fetchone()
        if exists:
            con.execute(
                "UPDATE cancellations SET mc_no=?, shop_no=?, chan_no=?, channel=?, "
                "cancel_date=?, cancel_gross=?, phone=?, name=?, status_raw=?, "
                "source=?, file_name=? WHERE cancel_key=?",
                (c.get("mc_no"), c.get("shop_no"), c.get("chan_no"), c.get("channel"),
                 c.get("cancel_date"), c.get("cancel_gross"), c.get("phone"), c.get("name"),
                 c.get("status_raw"), c.get("source"), c.get("file_name"), c["cancel_key"]),
            )
            updated += 1
        else:
            con.execute(
                "INSERT INTO cancellations(cancel_key, order_id, match_status, match_method, "
                "source, channel, cancel_date, cancel_gross, phone, name, mc_no, shop_no, "
                "chan_no, status_raw, file_name, first_seen_at) "
                "VALUES (?,NULL,'unmatched','',?,?,?,?,?,?,?,?,?,?,?,?)",
                (c["cancel_key"], c.get("source"), c.get("channel"), c.get("cancel_date"),
                 c.get("cancel_gross"), c.get("phone"), c.get("name"), c.get("mc_no"),
                 c.get("shop_no"), c.get("chan_no"), c.get("status_raw"), c.get("file_name"), now),
            )
            new += 1
    con.commit()
    return new, updated


def run(con: sqlite3.Connection, cfg: Config, recheck: bool = False) -> dict:
    """จับคู่ทะเบียนยกเลิกที่ยังไม่มีผล แล้วอัปเดตธงบนตาราง orders

    recheck=True  บังคับคำนวณใหม่ทั้งหมด (ใช้ตอนตรวจว่าผลยังเหมือนเดิม)
    """
    idx = _Index(con, cfg)
    now = util.now_str()
    stats = {"matched": 0, "unmatched": 0, "ambiguous": 0, "no_source_order": 0,
             "kept": 0, "by_method": {}}

    if recheck:
        con.execute(
            "UPDATE cancellations SET order_id=NULL, match_status='unmatched', "
            "match_method='', matched_at=NULL WHERE confirmed_by IS NULL OR confirmed_by='pipeline'"
        )
        con.execute("UPDATE orders SET is_cancelled=0, cancel_gross=NULL, cancel_method=NULL")
        con.commit()

    # ออเดอร์ที่ถูกจองไว้แล้วจากรอบก่อน ห้ามถูกหักซ้ำ
    taken: set[str] = {
        r["order_id"] for r in con.execute(
            "SELECT order_id FROM cancellations WHERE order_id IS NOT NULL"
        )
    }
    stats["kept"] = len(taken)

    pending = [dict(r) for r in con.execute(
        "SELECT * FROM cancellations WHERE order_id IS NULL ORDER BY cancel_date, cancel_key"
    )]

    amount_conflicts: list[tuple[str, float, float]] = []

    for rec in pending:
        order_id, method, verdict = _match_one(rec, idx, taken, cfg)
        if order_id:
            taken.add(order_id)
            con.execute(
                "UPDATE cancellations SET order_id=?, match_status='matched', match_method=?, "
                "matched_at=?, confirmed_by='pipeline' WHERE cancel_key=?",
                (order_id, method, now, rec["cancel_key"]),
            )
            order = idx.by_id.get(order_id, {})
            own_gross = util.rv(util.num(order.get("amount_inc_vat")))
            if rec.get("source") == "manual-cancel" and util.num(rec.get("cancel_gross")) > 0:
                # ไฟล์ยกเลิกของ Hylme ระบุยอดเงินจริงมาโดยตรง ใช้ยอดนี้เป็นยอดที่หัก
                #
                # ของเดิม (index.html บรรทัด 3930) เขียนทับ Lead Value ตรงนี้เลย
                # ที่นี่ไม่แตะยอดดิบใน orders — ความต่างจะถูกบันทึกเป็นแถว
                # manual_cancel_amount ใน revenue_adjustment โดย pipeline/revenue.py แทน
                # เพื่อให้ย้อนรอยได้ว่ายอดถูกปรับจากอะไรเป็นอะไร
                cancel_gross = util.rv(rec["cancel_gross"])
                if abs(cancel_gross - own_gross) > 0.01:
                    amount_conflicts.append((order_id, own_gross, cancel_gross))
            else:
                cancel_gross = own_gross
            con.execute(
                "UPDATE orders SET is_cancelled=1, cancel_gross=?, cancel_method=? WHERE order_id=?",
                (cancel_gross, method, order_id),
            )
            stats["matched"] += 1
            stats["by_method"][method] = stats["by_method"].get(method, 0) + 1
        else:
            # no_source_order = ออเดอร์ไม่เคยถูกนับเป็นยอดขาย จึงไม่มีอะไรให้หัก (ไม่ใช่งานค้าง)
            con.execute(
                "UPDATE cancellations SET match_status=?, matched_at=? WHERE cancel_key=?",
                (verdict, now, rec["cancel_key"]),
            )
            stats[verdict] = stats.get(verdict, 0) + 1

    con.commit()
    stats["amount_conflicts"] = amount_conflicts
    stats["total_in_registry"] = con.execute("SELECT COUNT(*) FROM cancellations").fetchone()[0]
    stats["still_unmatched"] = con.execute(
        "SELECT COUNT(*) FROM cancellations WHERE match_status='unmatched'"
    ).fetchone()[0]
    stats["still_ambiguous"] = con.execute(
        "SELECT COUNT(*) FROM cancellations WHERE match_status='ambiguous'"
    ).fetchone()[0]
    stats["no_source_total"] = con.execute(
        "SELECT COUNT(*) FROM cancellations WHERE match_status='no_source_order'"
    ).fetchone()[0]
    return stats
