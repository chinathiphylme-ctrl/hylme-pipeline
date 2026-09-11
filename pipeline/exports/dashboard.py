# -*- coding: utf-8 -*-
"""สร้างไฟล์ .json สำหรับแดชบอร์ด — ตัวเลขสรุปล้วน ไม่มีข้อมูลลูกค้า

นี่คือหัวใจของการแก้ปัญหาความเป็นส่วนตัว
ของเดิม แดชบอร์ดถือ 'ไฟล์ที่ import เข้ามาทั้งก้อน' ไว้ในตัว (EMBEDDED_DATA 27.5 MB
มีชื่อ เบอร์โทร ที่อยู่ อีเมล Line ID เลขประจำตัวผู้เสียภาษี ของลูกค้า 9,234 ราย)
แล้วปุ่ม 'ส่งข้อมูลขึ้น GitHub' ก็ push ก้อนเดียวกันนั้นขึ้นเว็บ

ไฟล์ในโฟลเดอร์นี้มีแต่ยอดรวมและจำนวน ไม่มีแถวของลูกค้าแม้แต่แถวเดียว
เอาขึ้นเว็บได้โดยไม่ต้องพึ่งความจำของใคร

หมายเหตุ: ยอดขายสุทธิ = ยอดรวม − ยอดที่ถูกยกเลิก (ตามกติกาเดิมของแดชบอร์ด)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .. import util
from ..config import Config

SCHEMA_VERSION = 1


def _round_dict(d: dict) -> dict:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def _rows_to_list(rows) -> list[dict]:
    return [_round_dict(dict(r)) for r in rows]


def build(con: sqlite3.Connection, cfg: Config, out_dir: Path) -> dict:
    """เขียนไฟล์ JSON ทั้งชุด คืนสรุปว่าเขียนอะไรไปบ้าง"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}

    def dump(name: str, payload) -> None:
        path = out_dir / name
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        written[name] = path.stat().st_size

    # ---- ยอดรายวัน ----
    daily = _rows_to_list(con.execute("""
        SELECT order_date                                            AS date,
               COUNT(*)                                              AS orders,
               SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 1 ELSE 0 END)        AS cancelled_orders,
               ROUND(SUM(rep_inc_vat),2)                          AS gross,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1
                              THEN COALESCE(cancel_gross,rep_inc_vat) ELSE 0 END),2) AS cancelled,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1
                              THEN 0 ELSE rep_inc_vat END),2)     AS net,
               SUM(CASE WHEN COALESCE(is_free,0)=1 THEN 1 ELSE 0 END) AS free_orders
        FROM v_order_reported WHERE order_date IS NOT NULL
        GROUP BY order_date ORDER BY order_date
    """))
    dump("daily.json", daily)

    # ---- ยอดรายวัน x ช่องทาง ----
    daily_channel = _rows_to_list(con.execute("""
        SELECT order_date AS date, channel,
               COUNT(*) AS orders,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2) AS net
        FROM v_order_reported WHERE order_date IS NOT NULL
        GROUP BY order_date, channel ORDER BY order_date, channel
    """))
    dump("daily_channel.json", daily_channel)

    # ---- ยอดรายวัน x ทีม ----
    daily_team = _rows_to_list(con.execute("""
        SELECT order_date AS date, team,
               COUNT(*) AS orders,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2) AS net
        FROM v_order_reported WHERE order_date IS NOT NULL
        GROUP BY order_date, team ORDER BY order_date, team
    """))
    dump("daily_team.json", daily_team)

    # ---- สินค้า (แยกตามชนิด) ----
    products = _rows_to_list(con.execute("""
        SELECT o.order_date AS date, i.product,
               ROUND(SUM(i.qty),0)             AS qty,
               ROUND(SUM(i.amount_inc_vat * o.rev_ratio),2) AS sales
        FROM order_items i JOIN v_order_reported o ON o.order_id = i.order_id
        WHERE COALESCE(o.is_cancelled,0) = 0 AND o.order_date IS NOT NULL
        GROUP BY o.order_date, i.product ORDER BY o.order_date, i.product
    """))
    dump("products.json", products)

    # ---- วิธีชำระเงิน ----
    payments = _rows_to_list(con.execute("""
        SELECT order_date AS date, payment_method,
               COUNT(*) AS orders,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2) AS net
        FROM v_order_reported WHERE order_date IS NOT NULL
        GROUP BY order_date, payment_method ORDER BY order_date
    """))
    dump("payments.json", payments)

    # ---- ผู้ดูแล (ชื่อพนักงาน ไม่ใช่ข้อมูลลูกค้า) ----
    owners = _rows_to_list(con.execute("""
        SELECT order_date AS date, owner, team,
               COUNT(*) AS orders,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2) AS net
        FROM v_order_reported WHERE order_date IS NOT NULL AND owner IS NOT NULL AND owner <> ''
        GROUP BY order_date, owner ORDER BY order_date
    """))
    dump("owners.json", owners)

    # ---- สรุปรายเดือน ----
    monthly = _rows_to_list(con.execute("""
        SELECT substr(order_date,1,7) AS month,
               COUNT(*)               AS orders,
               COUNT(DISTINCT customer_key) AS customers,
               ROUND(SUM(rep_inc_vat),2) AS gross,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1
                              THEN COALESCE(cancel_gross,rep_inc_vat) ELSE 0 END),2) AS cancelled,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2) AS net
        FROM v_order_reported WHERE order_date IS NOT NULL
        GROUP BY month ORDER BY month
    """))
    dump("monthly.json", monthly)

    # ---- การจัดส่ง ----
    delivery = _rows_to_list(con.execute("""
        SELECT event_date AS date, status, COUNT(DISTINCT order_id) AS orders
        FROM fulfilment_events GROUP BY event_date, status ORDER BY event_date
    """))
    dump("delivery.json", delivery)

    # ---- การกระทบยอดยกเลิก (นับอย่างเดียว ไม่มีชื่อ/เบอร์) ----
    recon = _rows_to_list(con.execute("""
        SELECT match_status, match_method, COUNT(*) AS n,
               ROUND(SUM(cancel_gross),2) AS gross
        FROM cancellations GROUP BY match_status, match_method
    """))
    dump("reconciliation.json", recon)

    # ---- ตรวจสอบย้อนกลับได้: ดิบ vs ที่แสดง vs รายงาน Excel ----
    # ไม่มีข้อมูลลูกค้า มีแต่ยอดรวมและชื่อกฎ จึงเอาขึ้นเว็บได้เหมือนไฟล์อื่นในโฟลเดอร์นี้
    audit_monthly = _rows_to_list(con.execute("""
        SELECT substr(o.order_date,1,7)              AS month,
               COUNT(*)                              AS orders,
               ROUND(SUM(r.raw_inc_vat),2)           AS raw,
               ROUND(SUM(r.reported_inc_vat),2)      AS reported,
               ROUND(SUM(r.total_delta),2)           AS delta,
               SUM(CASE WHEN r.n_adjustments > 0 THEN 1 ELSE 0 END) AS adjusted_orders,
               SUM(CASE WHEN r.data_state='pre_reconciled' THEN 1 ELSE 0 END) AS pre_reconciled
        FROM orders o JOIN order_revenue r ON r.order_id = o.order_id
        WHERE o.order_date IS NOT NULL
        GROUP BY month ORDER BY month
    """))
    targets = {r["m"]: r["t"] for r in con.execute(
        "SELECT substr(report_date,1,7) m, ROUND(SUM(target_inc_vat),2) t "
        "FROM sales_report_day WHERE channel='__TOTAL__' GROUP BY m")}
    for row in audit_monthly:
        row["sales_report"] = targets.get(row["month"])
        row["vs_report"] = (round(row["reported"] - row["sales_report"], 2)
                            if row["sales_report"] is not None else None)

    by_rule = _rows_to_list(con.execute("""
        SELECT rule, COUNT(*) AS n_orders, ROUND(SUM(delta),2) AS total_delta,
               MIN(source_kind) AS source_kind
        FROM revenue_adjustment GROUP BY rule ORDER BY ABS(SUM(delta)) DESC
    """))
    dump("revenue_audit.json", {
        "generated_at": util.now_str(),
        "note": ("ยอดดิบเก็บใน orders · การปรับทุกครั้งอยู่ใน revenue_adjustment · "
                 "ยอดที่แสดงอยู่ใน order_revenue — ไม่มีขั้นตอนไหนเขียนทับยอดดิบ"),
        "monthly": audit_monthly,
        "by_rule": by_rule,
        "layers": cfg.revenue["layers"],
        "calibration_until": cfg.revenue.get("calibration_until"),
        "mkp_fix_until": cfg.revenue.get("mkp_fix_until"),
    })

    # ---- สรุปภาพรวม ----
    span = con.execute(
        "SELECT MIN(order_date), MAX(order_date), COUNT(*) FROM v_order_reported WHERE order_date IS NOT NULL"
    ).fetchone()
    totals = con.execute("""
        SELECT ROUND(SUM(rep_inc_vat),2),
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2),
               COUNT(DISTINCT customer_key)
        FROM v_order_reported
    """).fetchone()
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": util.now_str(),
        "date_from": span[0],
        "date_to": span[1],
        "orders": span[2],
        "gross": totals[0],
        "net": totals[1],
        "customers": totals[2],
        "teams": [{"key": t["key"], "name": t["name"], "sub": t["sub"], "color": t["color"]}
                  for t in cfg.teams],
        "products": [p for p in cfg.products["product_order"]],
        "note": "ไฟล์ชุดนี้เป็นตัวเลขสรุปล้วน ไม่มีข้อมูลส่วนบุคคลของลูกค้า",
    }
    dump("summary.json", summary)

    return {"files": written, "days": len(daily), "months": len(monthly)}
