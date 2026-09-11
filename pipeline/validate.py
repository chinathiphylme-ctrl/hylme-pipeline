# -*- coding: utf-8 -*-
"""ตรวจความถูกต้องของข้อมูลก่อนสร้าง output

ทุกข้อที่ตรวจมาจากหัวข้อ 11 ของสเปก ผลการตรวจถูกบันทึกลงตาราง validation_log
เพื่อให้ตามงานต่อได้ ไม่หายไปพร้อมหน้าจอเหมือนแบนเนอร์เตือนของเดิม

ระดับ
    error    ต้องแก้ก่อนใช้งานไฟล์ output
    warning  ใช้งานต่อได้ แต่ควรตรวจ
    ok       ผ่าน

--------------------------------------------------------------------------
แยก "ปัญหาที่ยังแก้ได้" ออกจาก "ปัญหาของข้อมูลที่ต้นทางปิดไปแล้ว"
--------------------------------------------------------------------------
ปัญหาส่วนใหญ่ในฐานเป็นของระบบที่เลิกใช้ไปแล้ว (GoSell หยุด 18 มิ.ย. · Google Sheet หยุด
31 ก.ค. · ลีด ReadyPlanet `L0000...` หยุด 23 ก.ค.) ตามกลับไปแก้ต้นทางไม่ได้แล้ว
ถ้าเตือนซ้ำทุกวันมันจะกลบปัญหาของ Pancake/MyCloud ที่ยังแก้ได้จริง

    scope = historical   ต้นทางปิดแล้ว -> เก็บใน audit เฉย ๆ ไม่ขึ้นเป็นงานค้าง
    scope = current      ต้นทางยังอยู่  -> เตือนตามปกติ

**ตัดสินจาก `orders.source` เทียบกับ `config/validation.json -> live_sources`**
ไม่ใช้ prefix ของ order_id (source เก่ามี 12 แบบ ไม่มี prefix ร่วมกัน)
ไม่ใช้ `data_state` (GoSell = raw ส่วน Pancake = pre_reconciled ซึ่งกลับด้านกับที่ต้องการ)

**ไม่มีการปิดกฎข้อไหนทั้งกฎ** ทุกกฎยังตรวจครบเหมือนเดิม เปลี่ยนแค่ว่าอันไหนถูกยกขึ้นมาเป็น
งานที่ต้องทำ ถ้าวันหน้า Pancake มีปัญหาแบบเดียวกัน จะขึ้นเตือนทันที

--------------------------------------------------------------------------
วงจรของปัญหาหนึ่งอัน
--------------------------------------------------------------------------
    เจอครั้งแรก           -> NEW
    เจออีกในรอบถัดไป       -> ยังเป็นสถานะเดิม (ระบบไม่เคยรีเซ็ตสถานะที่คนตั้งไว้)
    คนสั่ง REVIEWED/IGNORED -> คงค่านั้นไว้ตลอด
    รอบไหนตรวจแล้วไม่เจอ   -> RESOLVED อัตโนมัติ (ยกเว้น IGNORED ที่ไม่แตะ)
    ปิดแล้วกลับมาอีก       -> NEW (ของจริงกลับมาแล้ว ต้องรู้)
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from . import db
from .config import Config

RE_PERIOD = re.compile(r"\d{4}-\d{2}(-\d{2})?")


@dataclass
class Finding:
    severity: str
    rule: str
    count: int                                  # จำนวนทั้งหมดที่กฎนี้เจอ
    detail: str
    sample: list[str]                           # ตัวอย่างจากฝั่งที่ต้องลงมือ
    actionable: int = 0                         # current + ยังไม่ได้ดู
    historical: int = 0                         # ต้นทางปิดแล้ว เก็บใน audit เฉย ๆ
    muted: int = 0                              # current แต่เคยดู/สั่งไม่ให้เตือนแล้ว
    entity_ids: list[str] = field(default_factory=list)


def _rows(con, sql, params=()) -> list:
    return con.execute(sql, params).fetchall()


def _chunks(seq, n=400):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _order_meta(con, ids: list[str]) -> dict:
    """order_id -> (source, order_date) เฉพาะ id ที่ต้องใช้"""
    out = {}
    for chunk in _chunks(sorted(set(ids))):
        marks = ",".join("?" for _ in chunk)
        for r in con.execute(
                f"SELECT order_id, source, order_date FROM orders WHERE order_id IN ({marks})",
                chunk):
            out[r["order_id"]] = (r["source"] or "", r["order_date"] or "")
    return out


def _cancel_meta(con, keys: list[str]) -> dict:
    """cancel_key -> cancel_date (ใช้กับกฎที่สิ่งที่ตรวจไม่ใช่ออเดอร์)"""
    out = {}
    if not keys:
        return out
    for chunk in _chunks(sorted(set(keys))):
        marks = ",".join("?" for _ in chunk)
        try:
            for r in con.execute(
                    f"SELECT cancel_key, cancel_date FROM cancellations "
                    f"WHERE cancel_key IN ({marks})", chunk):
                out[r["cancel_key"]] = r["cancel_date"] or ""
        except sqlite3.OperationalError:
            return out
    return out


def run(con: sqlite3.Connection, cfg: Config, run_id: int, since_days: int = 90) -> list[Finding]:
    """ตรวจข้อมูล

    since_days  ตรวจคุณภาพข้อมูลเฉพาะออเดอร์ในช่วง N วันล่าสุด (นับจากวันที่ล่าสุดในฐาน)
                ตั้งแต่มีการแยก historical/current แล้ว ใช้ 0 (ตรวจทั้งฐาน) ก็ไม่มีเสียงรบกวน
                เพิ่ม เพราะข้อมูลเก่าจะถูกจัดเป็น historical อยู่ดี
    ส่วนการตรวจเชิงโครงสร้าง (คีย์ซ้ำ ข้อมูลกำพร้า หักซ้ำ) ตรวจทั้งฐานเสมอ
    """
    findings: list[Finding] = []
    vcfg = cfg.validation
    live = cfg.live_sources
    hist_until = str(vcfg.get("historical_until") or "")
    max_listed = int(vcfg.get("max_listed_per_rule", 5))
    log_cap = int(vcfg.get("log_rows_per_rule", 200))

    since = ""
    if since_days and since_days > 0:
        row = con.execute("SELECT MAX(order_date) FROM orders").fetchone()
        if row and row[0]:
            import datetime as _dt
            try:
                since = (_dt.date.fromisoformat(row[0]) - _dt.timedelta(days=since_days)).isoformat()
            except ValueError:
                since = ""
    window = f" AND order_date >= '{since}'" if since else ""

    pending: list[dict] = []          # ปัญหาที่จะลงทะเบียนตอนท้าย
    checked_all: list[str] = []       # กฎที่ตรวจทั้งฐาน
    checked_window: list[str] = []    # กฎที่ตรวจเฉพาะช่วงวันล่าสุด

    def add(severity: str, rule: str, rows: list, detail_fmt: str, id_col: int = 0,
            windowed: bool = False):
        # จดไว้เสมอว่ากฎนี้ได้ตรวจแล้วในรอบนี้ แม้จะไม่เจออะไร — ใช้ตอนปิดปัญหาที่หายไป
        (checked_window if windowed else checked_all).append(rule)
        if not rows:
            return
        ids = [str(r[id_col]) for r in rows]
        ometa = _order_meta(con, ids)
        cmeta = _cancel_meta(con, [i for i in ids if i not in ometa])

        recs, act_ids, n_hist = [], [], 0
        for eid in ids:
            if eid in ometa:                                   # 1. เป็นออเดอร์ -> ใช้ source
                src, edate = ometa[eid]
                scope = "current" if src in live else "historical"
            elif RE_PERIOD.fullmatch(eid):                     # 2. เป็นเดือน/วันในรายงาน
                src, edate = "(ช่วงเวลาในรายงาน)", eid
                scope = "historical" if (hist_until and eid <= hist_until) else "current"
            elif eid in cmeta:                                 # 3. เป็นคีย์ทะเบียนยกเลิก
                src, edate = "(ทะเบียนยกเลิก)", cmeta[eid]
                scope = "historical" if (hist_until and edate and edate <= hist_until) else "current"
            else:                                              # 4. อย่างอื่น -> ถือว่ายังใช้อยู่
                src, edate, scope = "", "", "current"
            if scope == "historical":
                n_hist += 1
            else:
                act_ids.append(eid)
            recs.append({"rule": rule, "entity_id": eid, "scope": scope, "severity": severity,
                         "detail": detail_fmt.format(n=1), "entity_source": src,
                         "entity_date": edate})
        pending.extend(recs)

        # actionable/muted ยังเป็นค่าชั่วคราว — ต้องรู้สถานะในทะเบียนก่อนถึงจะสรุปได้
        # (ปัญหาที่เจ้าของสั่ง REVIEWED/IGNORED ไว้ ต้องไม่ถูกยกขึ้นมาเป็นงานอีก)
        # คำนวณจริงหลังลงทะเบียนเสร็จ ที่ท้ายฟังก์ชัน run()
        findings.append(Finding(
            severity=severity, rule=rule, count=len(ids),
            detail=detail_fmt.format(n=len(ids)), sample=act_ids[:max_listed],
            actionable=len(act_ids), historical=n_hist, entity_ids=act_ids))

        for rec in recs[:log_cap]:      # log จำกัดจำนวนต่อกฎต่อรอบ กันไฟล์บวม
            db.log_finding(con, run_id, severity, rule, rec["entity_id"],
                           rec["detail"], rec["scope"])

    # ---------------------------------------------------------- ออเดอร์
    add("error", "duplicate_order_id", _rows(con,
        "SELECT order_id, COUNT(*) c FROM orders GROUP BY order_id HAVING c > 1"),
        "order_id ซ้ำ {n} รายการ")

    add("error", "missing_order_date", _rows(con,
        "SELECT order_id FROM orders WHERE order_date IS NULL OR order_date=''"),
        "ออเดอร์ไม่มีวันที่ {n} รายการ")

    add("error", "invalid_order_date", _rows(con,
        "SELECT order_id FROM orders WHERE order_date IS NOT NULL AND order_date <> '' "
        "AND order_date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"),
        "รูปแบบวันที่ไม่ถูกต้อง {n} รายการ")

    add("error", "negative_amount", _rows(con,
        f"SELECT order_id FROM orders WHERE amount_inc_vat < 0{window}"),
        "ยอดเงินติดลบ {n} รายการ", windowed=bool(window))

    add("warning", "zero_amount_not_free", _rows(con,
        f"SELECT order_id FROM orders WHERE amount_inc_vat = 0 AND COALESCE(is_free,0) = 0{window}"),
        "ออเดอร์ยอด 0 บาทที่ไม่ได้ถูกทำเครื่องหมายว่าแจกฟรี {n} รายการ", windowed=bool(window))

    add("warning", "missing_customer", _rows(con,
        "SELECT order_id FROM orders WHERE (customer_key IS NULL OR customer_key='') "
        f"AND source NOT LIKE 'Marketplace%'{window}"),
        "ออเดอร์ที่ไม่มีเบอร์โทรใช้งานได้ {n} รายการ (marketplace ปิดบังเบอร์เป็นปกติ จึงไม่นับ)",
        windowed=bool(window))

    # ---------------------------------------------------------- บรรทัดสินค้า
    # บรรทัดที่ไม่ใช่สินค้า (ปรับเศษ/ส่วนลดระดับบิล/ค่าธรรมเนียม) ไม่มี SKU เป็นเรื่องปกติ
    # ตัดออกก่อนตรวจ — ไม่ได้ปิดกฎ แค่ไม่นับสิ่งที่ไม่ใช่สินค้าว่าเป็นสินค้าที่ขาด SKU
    non_prod = " AND ".join(
        ["LOWER(COALESCE(i.product_raw,'') || ' ' || COALESCE(i.product,'')) NOT LIKE ?"]
        * len(cfg.non_product_labels)) if cfg.non_product_labels else "1=1"
    add("warning", "missing_sku", _rows(con,
        "SELECT DISTINCT i.order_id FROM order_items i JOIN orders o ON o.order_id = i.order_id "
        f"WHERE (i.sku IS NULL OR i.sku='') AND {non_prod}"
        f"{window.replace('order_date', 'o.order_date')}",
        tuple(f"%{lbl}%" for lbl in cfg.non_product_labels)),
        "ออเดอร์ที่มีบรรทัดสินค้าไม่มี SKU {n} รายการ", windowed=bool(window))

    add("warning", "orphan_items", _rows(con,
        "SELECT DISTINCT i.order_id FROM order_items i "
        "LEFT JOIN orders o ON o.order_id = i.order_id WHERE o.order_id IS NULL"),
        "บรรทัดสินค้าที่ไม่มีออเดอร์ต้นทาง {n} รายการ")

    add("warning", "item_total_mismatch", _rows(con,
        "SELECT o.order_id FROM orders o JOIN ("
        "  SELECT order_id, ROUND(SUM(amount_ex_vat),2) s FROM order_items GROUP BY order_id"
        ") t ON t.order_id = o.order_id "
        f"WHERE ABS(t.s - o.amount_ex_vat) > 1.0{window.replace('order_date', 'o.order_date')}"),
        "ยอดรวมบรรทัดสินค้าไม่ตรงกับยอดออเดอร์เกิน 1 บาท {n} รายการ", windowed=bool(window))

    # ---------------------------------------------------------- การจัดส่ง
    add("warning", "duplicate_tracking", _rows(con,
        "SELECT tracking_no, COUNT(DISTINCT order_id) c FROM orders "
        "WHERE tracking_no IS NOT NULL AND tracking_no <> '' AND tracking_no <> 'N/A' "
        "GROUP BY tracking_no HAVING c > 1"),
        "เลขพัสดุเดียวกันถูกใช้กับหลายออเดอร์ {n} เลข")

    add("warning", "delivery_without_order", _rows(con,
        "SELECT DISTINCT e.order_id FROM fulfilment_events e "
        "LEFT JOIN orders o ON o.order_id = e.order_id WHERE o.order_id IS NULL"),
        "ข้อมูลการจัดส่งที่หาออเดอร์ต้นทางไม่พบ {n} รายการ")

    # ---------------------------------------------------------- การกระทบยอดยกเลิก
    add("warning", "cancel_unmatched", _rows(con,
        "SELECT cancel_key FROM cancellations WHERE match_status='unmatched'"),
        "ออเดอร์ยกเลิกที่จับคู่ไม่ได้ {n} รายการ")

    add("warning", "cancel_ambiguous", _rows(con,
        "SELECT cancel_key FROM cancellations WHERE match_status='ambiguous'"),
        "ออเดอร์ยกเลิกที่มีผู้สมัครหลายราย {n} รายการ — ต้องเลือกเอง")

    add("error", "cancel_double_deduct", _rows(con,
        "SELECT order_id, COUNT(*) c FROM cancellations "
        "WHERE order_id IS NOT NULL GROUP BY order_id HAVING c > 1"),
        "ออเดอร์เดียวถูกหักยกเลิกซ้ำ {n} รายการ")

    # ---------------------------------------------------------- ต้นทางหลายระบบ
    add("warning", "source_conflict", _rows(con,
        "SELECT external_id, COUNT(DISTINCT order_id) c FROM order_sources "
        "GROUP BY system, external_id HAVING c > 1"),
        "เลขอ้างอิงเดียวกันชี้ไปหลายออเดอร์ {n} รายการ")

    # ---------------------------------------------------------- ช่องทาง / สถานะ
    add("warning", "unknown_channel", _rows(con,
        f"SELECT order_id FROM orders WHERE channel = ?{window}",
        (cfg.channels["unknown_channel_label"],)),
        "ออเดอร์ที่ระบุช่องทางไม่ได้ {n} รายการ", windowed=bool(window))

    # ---------------------------------------------------------- ชั้นกระทบยอดรายได้
    add("error", "sales_report_channel_sum", _rows(con, """
        SELECT t.report_date FROM
          (SELECT report_date, target_inc_vat FROM sales_report_day WHERE channel='__TOTAL__') t
          JOIN (SELECT report_date, SUM(target_inc_vat) s FROM sales_report_day
                WHERE channel <> '__TOTAL__' GROUP BY report_date) c
            ON c.report_date = t.report_date
        WHERE ABS(c.s - t.target_inc_vat) > 0.5"""),
        "รายงานขาย: ผลรวมช่องทางไม่เท่ายอดรวมของวัน {n} วัน")

    add("warning", "sales_report_gap", _rows(con, f"""
        SELECT DISTINCT o.order_date FROM orders o
        WHERE o.order_date IS NOT NULL{window.replace('order_date', 'o.order_date')}
          AND o.data_state = 'raw'
          AND o.order_date <= (SELECT MAX(report_date) FROM sales_report_day)
          AND NOT EXISTS (SELECT 1 FROM sales_report_day s
                          WHERE s.report_date = o.order_date AND s.channel='__TOTAL__')"""),
        "วันที่มีออเดอร์ดิบแต่ไม่มีเป้าในรายงานขาย {n} วัน", windowed=bool(window))

    add("warning", "calibration_large_ratio", _rows(con, """
        SELECT order_id FROM revenue_adjustment
        WHERE rule='daily_calibration' AND ratio IS NOT NULL AND ABS(ratio - 1) > 0.2"""),
        "ออเดอร์ที่ถูกเกลี่ยเกิน ±20% {n} รายการ — อาจจับช่องทางผิด")

    add("warning", "revenue_truth_missing", _rows(con, f"""
        SELECT o.order_id FROM orders o
        WHERE o.data_state='raw' AND o.order_id LIKE 'MKP-%'
          AND substr(o.order_date,1,7) <= '{cfg.revenue['mkp_fix_until']}'
          AND NOT EXISTS (SELECT 1 FROM revenue_adjustment a
                          WHERE a.order_id = o.order_id AND a.rule IN ('mkp_settled','mkp_factor'))"""),
        "ออเดอร์ marketplace ในช่วงที่ควรมียอดจริง แต่ยังไม่มีใน revenue_truth {n} รายการ")

    add("warning", "reported_vs_report", _rows(con, """
        SELECT t.m FROM
          (SELECT substr(report_date,1,7) m, SUM(target_inc_vat) s FROM sales_report_day
           WHERE channel='__TOTAL__' GROUP BY m) t
          JOIN (SELECT substr(order_date,1,7) m, SUM(reported_inc_vat) s FROM order_revenue
                GROUP BY m) r ON r.m = t.m
        WHERE ABS(r.s - t.s) > 1.0"""),
        "ยอดที่แสดงรายเดือนไม่ตรงรายงานขาย Excel เกิน 1 บาท {n} เดือน")

    # ---------------------------------------------------------- ตรวจตัวลิสต์เอง
    # ถ้ามี source ที่ไม่อยู่ใน live_sources แต่ยังมีออเดอร์ใหม่ ๆ แปลว่าน่าจะเป็นระบบที่ยัง
    # ใช้งานอยู่แต่ลืมใส่ในลิสต์ -> ปัญหาของมันจะถูกเงียบทั้งที่ยังตามไปแก้ได้ ต้องเตือน
    stale_days = int(vcfg.get("stale_source_days", 21))
    mx = con.execute("SELECT MAX(order_date) FROM orders").fetchone()
    if mx and mx[0] and stale_days > 0:
        import datetime as _dt
        try:
            cut = (_dt.date.fromisoformat(mx[0]) - _dt.timedelta(days=stale_days)).isoformat()
        except ValueError:
            cut = ""
        if cut:
            suspects = [r for r in con.execute(
                "SELECT source, MAX(order_date) d, COUNT(*) n FROM orders "
                "GROUP BY source HAVING d >= ?", (cut,)) if (r["source"] or "") not in live]
            if suspects:
                names = " · ".join(f"{r['source']} (ล่าสุด {r['d']})" for r in suspects[:5])
                findings.append(Finding(
                    "warning", "source_not_in_live_list", len(suspects),
                    f"มี {len(suspects)} source ที่ยังมีออเดอร์ใหม่แต่ไม่อยู่ใน live_sources "
                    f"— ปัญหาของมันจะถูกจัดเป็นข้อมูลเก่า: {names}",
                    [str(r["source"]) for r in suspects[:max_listed]],
                    actionable=len(suspects)))

    # ---------------------------------------------------------- ลงทะเบียนปัญหา
    reg = db.upsert_issues(con, pending, run_id)
    reg["closed"] = (db.close_missing_issues(con, checked_all, run_id)
                     + db.close_missing_issues(con, checked_window, run_id, since=since))
    con.commit()

    # ---- หักปัญหาที่เจ้าของจัดการไปแล้วออกจากรายการที่ต้องลงมือ ----
    # ทำหลังลงทะเบียน เพราะต้องอ่านสถานะล่าสุดจากทะเบียน ไม่ใช่เดาเอง
    # ผลคือ REVIEWED / IGNORED จะไม่ถูกยกขึ้นมาเตือนอีก แต่ยังนับอยู่ในสรุป
    rules_seen = sorted({f.rule for f in findings if f.entity_ids})
    status_of: dict[tuple[str, str], str] = {}
    if rules_seen:
        marks = ",".join("?" for _ in rules_seen)
        status_of = {(r["rule"], r["entity_id"]): r["status"] for r in con.execute(
            f"SELECT rule, entity_id, status FROM validation_issue WHERE rule IN ({marks})",
            rules_seen)}
    for f in findings:
        if not f.entity_ids:
            continue
        still_new = [e for e in f.entity_ids if status_of.get((f.rule, e), "NEW") == "NEW"]
        f.muted = len(f.entity_ids) - len(still_new)
        f.actionable = len(still_new)
        f.entity_ids = still_new
        f.sample = still_new[:max_listed]

    if not any(f.actionable for f in findings):
        findings.append(Finding("ok", "all_checks_passed", 0,
                                f"ไม่มีปัญหาที่ต้องลงมือ (ตรวจช่วง {since or 'ทั้งฐาน'} เป็นต้นมา)",
                                []))
    return findings


def summary_counts(findings: list[Finding]) -> dict:
    """นับเฉพาะฝั่งที่ต้องลงมือ — ข้อมูลเก่าไม่ทำให้ pipeline ขึ้นสถานะว่ามีข้อผิดพลาด"""
    return {
        "error": sum(1 for f in findings if f.severity == "error" and f.actionable),
        "warning": sum(1 for f in findings if f.severity == "warning" and f.actionable),
        "historical": sum(f.historical for f in findings),
    }
