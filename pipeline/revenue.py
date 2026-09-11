# -*- coding: utf-8 -*-
"""ชั้นกระทบยอดรายได้ — พอร์ตจาก index.html recomputeDataset() บรรทัด 6546-6632

หลักการที่ต่างจากของเดิม (ตามที่ตกลงกัน)
    ของเดิม  เขียนทับ Lead Value ในหน่วยความจำ แล้วยอดดิบหายไป
    ที่นี่    orders เก็บยอดดิบเท่านั้น ทุกการปรับเป็นแถวใน revenue_adjustment
             และยอดที่แสดงอยู่ใน order_revenue ซึ่งสร้างใหม่ทั้งตารางทุกรอบ

ชั้นการปรับ
    1  mkp_settled          ยอดจริงรายออเดอร์จากรายงานแพลตฟอร์ม (revenue_truth)
    2  mkp_factor           ตัวคูณรายเดือนสำรอง เมื่อไม่มียอดจริงรายออเดอร์
    3  daily_calibration    เกลี่ยให้ยอดรายวัน x ช่องทาง ตรงรายงานขาย Excel
    4  manual_cancel_amount ไฟล์ยกเลิกของ Hylme ระบุยอดจริงมาโดยตรง
    4  manual_override      คนแก้เอง (ไม่ถูกลบตอนคำนวณใหม่)

*** ข้อมูลที่ผ่านการกระทบยอดมาแล้ว ***
ออเดอร์ที่ data_state = 'pre_reconciled' คือแถวที่โหลดเข้าฐานก่อนมี pipeline
ซึ่งมาจากไฟล์ export ของแดชบอร์ด = ยอดหลังเกลี่ยแล้ว ไม่ใช่ยอดดิบ
ชั้นที่ 1-3 จะข้ามออเดอร์กลุ่มนี้ทั้งหมด เพื่อไม่ให้เกลี่ยซ้ำ
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import util
from .config import Config

AUTO_RULES = ("mkp_settled", "mkp_factor", "daily_calibration", "manual_cancel_amount")
RULE_LAYER = {"mkp_settled": 1, "mkp_factor": 2, "daily_calibration": 3,
              "manual_cancel_amount": 4, "manual_override": 4}
EPS = 1e-9


# ---------------------------------------------------------------- ช่องทางของรายงาน

def xl_bucket_of(cfg: Config, order: dict) -> str | None:
    """ออเดอร์นี้ตรงกับช่องทางไหนในรายงานขาย (index.html: xlBucketOf)

    คืน None = อ่านช่องทางไม่ออก -> จะถูกโยนเข้าช่องทางที่ยังขาดยอดในขั้นตอนถัดไป
    """
    rr = cfg.revenue
    gs = util.norm(order.get("gosell_channel"))
    if gs and gs in rr["xl_gs_map"]:
        return rr["xl_gs_map"][gs]

    s = str(order.get("lead_intro") or "")
    if s:
        if re.search(r"facebook", s, re.I):
            return "Facebook"
        if re.search(r"shopee", s, re.I):
            return "Shopee"
        if re.search(r"tiktok", s, re.I):
            return "Tiktok"
        if re.search(r"lazada", s, re.I):
            return "Lazada"
        if re.search(r"\bLINE\b", s):
            return "Line OA"
        if re.search(r"\bCRM\b", s, re.I):
            return "CRM"
        if re.search(r"instagram|\bIG\b", s, re.I):
            return "Instagram"
        if re.search(r"website|sale ?page|woo", s, re.I):
            return "Sale Page"

    # ชั้นสำรองที่เพิ่มใหม่ — ออเดอร์เก่าไม่มี Lead Intro มีแต่คอลัมน์ channel
    return rr["xl_channel_map"].get(util.norm(order.get("channel"))) or None


# ---------------------------------------------------------------- ตัวช่วย

class _Ledger:
    """สะสมแถว adjustment ไว้ก่อน แล้วเขียนทีเดียวตอนจบ"""

    def __init__(self, run_id: int):
        self.rows: list[tuple] = []
        self.run_id = run_id
        self.by_rule: dict[str, dict] = {}

    def add(self, order_id: str, rule: str, before: float, after: float,
            source_kind: str = "", source_ref: str = "", bucket: str = "",
            note: str = "", order_date: str | None = None) -> float:
        before = util.rv(before)
        after = util.rv(after)
        if abs(after - before) < 0.005:
            return before                     # ไม่มีการเปลี่ยนแปลง ไม่ต้องบันทึก
        delta = util.rv(after - before)
        ratio = (after / before) if abs(before) > EPS else None
        self.rows.append((order_id, RULE_LAYER[rule], rule, before, after, delta, ratio,
                          source_kind, source_ref, bucket, self.run_id, util.now_str(), note))
        st = self.by_rule.setdefault(rule, {"n": 0, "delta": 0.0, "from": None, "to": None})
        st["n"] += 1
        st["delta"] = util.rv(st["delta"] + delta)
        if order_date:
            st["from"] = order_date if st["from"] is None else min(st["from"], order_date)
            st["to"] = order_date if st["to"] is None else max(st["to"], order_date)
        return after

    def flush(self, con: sqlite3.Connection) -> None:
        con.executemany(
            "INSERT INTO revenue_adjustment(order_id, layer, rule, amount_before, amount_after, "
            "delta, ratio, source_kind, source_ref, bucket, run_id, computed_at, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(order_id, layer, rule) DO UPDATE SET "
            "amount_before=excluded.amount_before, amount_after=excluded.amount_after, "
            "delta=excluded.delta, ratio=excluded.ratio, source_kind=excluded.source_kind, "
            "source_ref=excluded.source_ref, bucket=excluded.bucket, run_id=excluded.run_id, "
            "computed_at=excluded.computed_at, note=excluded.note",
            self.rows,
        )


def _load_orders(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT order_id, order_date, amount_inc_vat, amount_ex_vat, channel, lead_intro, "
        "       gosell_channel, source, COALESCE(data_state,'raw') AS data_state, "
        "       COALESCE(is_cancelled,0) AS is_cancelled "
        "FROM orders WHERE order_date IS NOT NULL ORDER BY order_date, order_id"
    )]


def _settled_index(con: sqlite3.Connection) -> dict[str, float]:
    """เลขอ้างอิงของแพลตฟอร์ม -> ยอดที่จ่ายจริง"""
    out = {}
    for r in con.execute("SELECT external_id, settled_gross FROM revenue_truth"):
        if r["settled_gross"] is not None:
            out[str(r["external_id"])] = float(r["settled_gross"])
    return out


def _external_ids(con: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in con.execute("SELECT order_id, external_id FROM order_sources"):
        out.setdefault(r["order_id"], []).append(str(r["external_id"]))
    return out


def _targets(con: sqlite3.Connection) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    total: dict[str, float] = {}
    per_ch: dict[str, dict[str, float]] = {}
    for r in con.execute("SELECT report_date, channel, target_inc_vat FROM sales_report_day"):
        if r["channel"] == "__TOTAL__":
            total[r["report_date"]] = float(r["target_inc_vat"])
        else:
            per_ch.setdefault(r["report_date"], {})[r["channel"]] = float(r["target_inc_vat"])
    return total, per_ch


# ---------------------------------------------------------------- เครื่องหลัก

def rebuild(con: sqlite3.Connection, cfg: Config, run_id: int,
            enabled: bool | None = None) -> dict:
    """คำนวณชั้นกระทบยอดใหม่ทั้งหมด แล้วเขียน revenue_adjustment + order_revenue

    enabled=False -> ข้ามการปรับทั้งหมด (ยอดที่แสดง = ยอดดิบ) ใช้กับ --no-reconcile
    """
    rr = cfg.revenue
    if enabled is None:
        enabled = bool(rr.get("enabled", True))
    layers = rr["layers"]
    stats = {"enabled": enabled, "orders": 0, "adjusted": 0, "skipped_pre_reconciled": 0,
             "by_rule": {}, "calibrated_days": 0, "no_target_days": 0,
             "orphan_channels": {}, "large_ratio": [], "skipped_ratio": []}

    # ---- ลบผลของกฎอัตโนมัติทิ้งก่อน แล้วคำนวณใหม่ (manual_override ไม่ถูกแตะ) ----
    con.execute(
        "DELETE FROM revenue_adjustment WHERE rule IN (%s)"
        % ",".join("?" for _ in AUTO_RULES), AUTO_RULES
    )

    orders = _load_orders(con)
    stats["orders"] = len(orders)
    by_id = {o["order_id"]: o for o in orders}
    current = {o["order_id"]: util.rv(o["amount_inc_vat"]) for o in orders}
    locked: set[str] = set()
    ledger = _Ledger(run_id)

    if enabled:
        settled = _settled_index(con)
        ext_ids = _external_ids(con)
        mkp_channels = set(rr["mkp_channels"])
        mkp_factor = rr.get("mkp_factor") or {}
        fix_until = rr["mkp_fix_until"]

        # ------------------------------------------------ ชั้น 1-2 : marketplace
        if layers.get("mkp_settled") or layers.get("mkp_factor"):
            for o in orders:
                if o["data_state"] != "raw":
                    continue
                chan = util.norm(o["gosell_channel"]) or util.norm(o["channel"])
                if chan not in mkp_channels:
                    continue
                month = (o["order_date"] or "")[:7]
                if not month or month > fix_until:
                    continue
                gross = current[o["order_id"]]
                if gross <= 0:
                    continue

                truth = None
                ref = ""
                for eid in ext_ids.get(o["order_id"], []) + [o["order_id"].replace("MKP-", "")]:
                    if eid in settled:
                        truth = settled[eid]
                        ref = f"{chan.lower()}/{eid}"
                        break

                if truth is not None and truth > 0 and layers.get("mkp_settled"):
                    current[o["order_id"]] = ledger.add(
                        o["order_id"], "mkp_settled", gross, truth,
                        "platform_report", ref, order_date=o["order_date"])
                    locked.add(o["order_id"])
                elif layers.get("mkp_factor"):
                    f = mkp_factor.get(f"{chan}|{month}")
                    if f is None:
                        continue
                    current[o["order_id"]] = ledger.add(
                        o["order_id"], "mkp_factor", gross, gross * float(f),
                        "platform_report", f"{chan}|{month}", note=f"factor={f}",
                        order_date=o["order_date"])
                    locked.add(o["order_id"])

        # ------------------------------------------------ ชั้น 3 : เกลี่ยรายวัน
        bucket_raw: dict[str, str | None] = {}
        bucket_used: dict[str, str | None] = {}
        if layers.get("daily_calibration"):
            day_total, day_ch = _targets(con)
            cal_until = rr.get("calibration_until") or "9999-12"
            respect_lock = bool(rr.get("respect_mkp_lock", False))
            warn_at = float(rr.get("large_ratio_warn", 0.2))
            max_ratio = float(rr.get("calibration_max_ratio", 0) or 0)

            by_day: dict[str, list[dict]] = {}
            for o in orders:
                d = o["order_date"]
                if o["data_state"] != "raw":
                    stats["skipped_pre_reconciled"] += 1
                    continue
                if not d or d[:7] > cal_until:
                    continue
                if d not in day_total:
                    stats["no_target_days"] += 1
                    continue
                bucket_raw[o["order_id"]] = xl_bucket_of(cfg, o)
                by_day.setdefault(d, []).append(o)

            for day, rows in sorted(by_day.items()):
                tgt = day_ch.get(day, {})
                pool_rows = []
                cur: dict[str, float] = {}
                assign: dict[str, str | None] = {}

                # 3.1 ช่องทางที่รายงานไม่ได้ลงยอดไว้เลยในวันนั้น -> โยนกลับเข้ากองรอจัด
                for o in rows:
                    b = bucket_raw.get(o["order_id"])
                    if b and not (tgt.get(b, 0) > 0):
                        b = None
                    assign[o["order_id"]] = b
                    if b:
                        cur[b] = cur.get(b, 0.0) + current[o["order_id"]]
                    else:
                        pool_rows.append(o)

                # ออเดอร์ที่อ่านช่องทางไม่ออก -> ลงช่องทางที่ยอดยังขาดมากที่สุด
                pool_rows.sort(key=lambda o: current[o["order_id"]], reverse=True)
                for o in pool_rows:
                    pick, worst = None, float("-inf")
                    for b in rr["xl_buckets"]:
                        if not (tgt.get(b, 0) > 0):
                            continue
                        short = tgt.get(b, 0.0) - cur.get(b, 0.0)
                        if short > worst:
                            worst, pick = short, b
                    if pick is None:
                        for b in rr["xl_buckets"]:
                            if cur.get(b, 0) > 0:
                                pick = b
                                break
                    pick = pick or "Other"
                    assign[o["order_id"]] = pick
                    cur[pick] = cur.get(pick, 0.0) + current[o["order_id"]]

                # 3.2 ช่องทางที่รายงานมียอดแต่วันนั้นไม่มีออเดอร์เลย -> เกลี่ยเข้าช่องทางอื่น
                placeable = orphan = 0.0
                for b in rr["xl_buckets"]:
                    if cur.get(b, 0) > 0:
                        placeable += tgt.get(b, 0.0)
                    elif tgt.get(b, 0):
                        orphan += tgt[b]
                        stats["orphan_channels"][b] = util.rv(
                            stats["orphan_channels"].get(b, 0.0) + tgt[b])
                spread = ((placeable + orphan) / placeable) if placeable > 0 else 1.0

                # 3.3 ย่อ/ขยายออเดอร์ในแต่ละช่องทางให้ยอดช่องทางนั้นเท่ากับเป้า
                for b in rr["xl_buckets"]:
                    if not (cur.get(b, 0) > 0):
                        continue
                    want = tgt.get(b, 0.0) * spread
                    ratio = want / cur[b]
                    if ratio < 0 or abs(ratio - 1) < 1e-12:
                        continue
                    # กันเคส "ยอดดิบของวันนั้นยังมาไม่ครบ"
                    # ถ้าโหลดไฟล์ต้นทางมาแค่บางส่วน ยอดดิบจะน้อยกว่าเป้ามาก การเกลี่ยจะ
                    # ดันออเดอร์ไม่กี่ใบที่มีให้พองขึ้นหลายเท่า -> ยอดที่แสดงผิดแบบเงียบ ๆ
                    # เกินเพดานเมื่อไร ปล่อยให้ยอดดิบเป็นยอดที่แสดง แล้วรายงานให้เห็น
                    if max_ratio and (ratio > max_ratio or ratio < 1 / max_ratio):
                        if len(stats["skipped_ratio"]) < 50:
                            stats["skipped_ratio"].append(
                                {"date": day, "bucket": b, "ratio": round(ratio, 4),
                                 "orders": sum(1 for o in rows
                                               if assign.get(o["order_id"]) == b),
                                 "raw": util.rv(cur[b]), "target": util.rv(want)})
                        continue
                    for o in rows:
                        if assign.get(o["order_id"]) != b:
                            continue
                        if respect_lock and o["order_id"] in locked:
                            continue
                        before = current[o["order_id"]]
                        current[o["order_id"]] = ledger.add(
                            o["order_id"], "daily_calibration", before, before * ratio,
                            "sales_report_excel", f"{day}|{b}", bucket=b,
                            order_date=day)
                        if abs(ratio - 1) > warn_at and len(stats["large_ratio"]) < 50:
                            stats["large_ratio"].append(
                                {"order_id": o["order_id"], "date": day, "bucket": b,
                                 "ratio": round(ratio, 4)})
                stats["calibrated_days"] += 1
                for o in rows:
                    bucket_used[o["order_id"]] = assign.get(o["order_id"])

        # ------------------------------------------------ ชั้น 4 : ไฟล์ยกเลิกระบุยอดจริง
        if layers.get("manual_cancel_amount"):
            for r in con.execute(
                "SELECT order_id, cancel_gross, file_name FROM cancellations "
                "WHERE order_id IS NOT NULL AND source='manual-cancel' AND cancel_gross > 0"
            ):
                oid = r["order_id"]
                if oid not in current:
                    continue
                current[oid] = ledger.add(
                    oid, "manual_cancel_amount", current[oid], float(r["cancel_gross"]),
                    "cancel_file", r["file_name"] or "",
                    note="ไฟล์ยกเลิกของ Hylme ระบุยอดเงินจริง",
                    order_date=by_id[oid]["order_date"])
    else:
        bucket_raw, bucket_used = {}, {}

    # ---- manual_override มีลำดับสูงสุดเสมอ แม้ปิดชั้นอื่น ----
    for r in con.execute(
        "SELECT order_id, amount_after FROM revenue_adjustment WHERE rule='manual_override'"
    ):
        if r["order_id"] in current:
            current[r["order_id"]] = util.rv(r["amount_after"])

    ledger.flush(con)

    # ---- สร้าง order_revenue ใหม่ทั้งตาราง ----
    applied: dict[str, list[str]] = {}
    n_adj: dict[str, int] = {}
    for r in con.execute(
        "SELECT order_id, rule FROM revenue_adjustment ORDER BY order_id, layer"
    ):
        applied.setdefault(r["order_id"], []).append(r["rule"])
        n_adj[r["order_id"]] = n_adj.get(r["order_id"], 0) + 1

    vat = cfg.vat_rate
    now = util.now_str()
    con.execute("DELETE FROM order_revenue")
    con.executemany(
        "INSERT INTO order_revenue(order_id, order_date, raw_inc_vat, raw_ex_vat, "
        "reported_inc_vat, reported_ex_vat, total_ratio, total_delta, n_adjustments, "
        "rules_applied, is_locked, data_state, xl_bucket_raw, xl_bucket_used, run_id, computed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(
            o["order_id"], o["order_date"],
            util.rv(o["amount_inc_vat"]), util.rv(o["amount_ex_vat"]),
            current[o["order_id"]], util.rv(current[o["order_id"]] / (1 + vat)),
            (round(current[o["order_id"]] / o["amount_inc_vat"], 8)
             if o["amount_inc_vat"] else None),
            util.rv(current[o["order_id"]] - util.rv(o["amount_inc_vat"])),
            n_adj.get(o["order_id"], 0),
            ">".join(applied.get(o["order_id"], [])),
            1 if o["order_id"] in locked else 0,
            o["data_state"],
            bucket_raw.get(o["order_id"]), bucket_used.get(o["order_id"]),
            run_id, now,
        ) for o in orders],
    )

    # ---- สรุปรายกฎ ----
    con.execute("DELETE FROM revenue_rule_run WHERE run_id=?", (run_id,))
    for rule, st in ledger.by_rule.items():
        con.execute(
            "INSERT INTO revenue_rule_run(run_id, rule, n_orders, total_delta, "
            "date_from, date_to, detail) VALUES (?,?,?,?,?,?,?)",
            (run_id, rule, st["n"], st["delta"], st["from"], st["to"],
             json.dumps({"orphan_channels": stats["orphan_channels"]}, ensure_ascii=False)
             if rule == "daily_calibration" else None),
        )
    con.commit()

    stats["by_rule"] = {k: {"n": v["n"], "delta": v["delta"]} for k, v in ledger.by_rule.items()}
    stats["adjusted"] = len({r[0] for r in ledger.rows})
    row = con.execute(
        "SELECT ROUND(SUM(raw_inc_vat),2), ROUND(SUM(reported_inc_vat),2) FROM order_revenue"
    ).fetchone()
    stats["sum_raw"] = row[0]
    stats["sum_reported"] = row[1]
    return stats
