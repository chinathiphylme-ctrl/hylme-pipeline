# -*- coding: utf-8 -*-
"""ไฟล์ตั้งต้นสำหรับทีมเทเลเซลและโมเดลให้คะแนน

ของเดิม: export ไฟล์ .xlsx จากแดชบอร์ด -> เปลี่ยนชื่อ -> คัดลอกเข้าโฟลเดอร์ hylme_engine
         -> รัน run_daily.py (ขั้นตอนที่ 19-22 ของ workflow เดิม)
ที่นี่:   อ่านจาก master database ตรง ๆ ไม่มีการยกไฟล์ข้ามโฟลเดอร์

*** ไฟล์ในโฟลเดอร์นี้มีชื่อและเบอร์โทรลูกค้า — เป็นข้อมูลส่วนบุคคล ห้ามขึ้น Git ***
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .. import util
from ..config import Config

CUSTOMER_SQL = """
SELECT o.customer_key                                   AS customer_key,
       MAX(o.name_raw)                                  AS name,
       MAX(o.phone_clean)                               AS phone,
       MIN(o.order_date)                                AS first_order,
       MAX(o.order_date)                                AS last_order,
       COUNT(*)                                         AS n_orders,
       ROUND(SUM(o.amount_inc_vat), 2)                  AS total_spend,
       ROUND(AVG(o.amount_inc_vat), 2)                  AS aov,
       ROUND(MAX(CASE WHEN o.order_date = last_o.d THEN o.amount_inc_vat END), 2) AS last_order_amount,
       MAX(o.channel)                                   AS last_channel,
       MAX(o.team)                                      AS last_team,
       MAX(o.province)                                  AS province
FROM orders o
JOIN (SELECT customer_key, MAX(order_date) d FROM orders
      WHERE customer_key IS NOT NULL AND COALESCE(is_cancelled,0)=0
      GROUP BY customer_key) last_o
  ON last_o.customer_key = o.customer_key
WHERE o.customer_key IS NOT NULL AND COALESCE(o.is_cancelled,0) = 0
GROUP BY o.customer_key
"""


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig เพื่อให้ Excel บน Windows เปิดภาษาไทยได้ถูกต้อง
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def build(con: sqlite3.Connection, cfg: Config, out_dir: Path, as_of: str | None = None) -> dict:
    out_dir = Path(out_dir)
    as_of = as_of or util.today_str()
    written = {}

    customers = [dict(r) for r in con.execute(CUSTOMER_SQL)]

    # ---- สินค้าล่าสุดของลูกค้าแต่ละราย (ใช้คำนวณวันของหมด) ----
    last_products: dict[str, list[str]] = {}
    for r in con.execute("""
        SELECT o.customer_key, i.product, SUM(i.qty) AS qty
        FROM orders o JOIN order_items i ON i.order_id = o.order_id
        JOIN (SELECT customer_key, MAX(order_date) d FROM orders
              WHERE customer_key IS NOT NULL AND COALESCE(is_cancelled,0)=0
              GROUP BY customer_key) last_o
          ON last_o.customer_key = o.customer_key AND last_o.d = o.order_date
        WHERE o.customer_key IS NOT NULL AND COALESCE(o.is_cancelled,0)=0
        GROUP BY o.customer_key, i.product
    """):
        last_products.setdefault(r["customer_key"], []).append(
            f"{r['product']} x{int(util.num(r['qty']))}"
        )

    # ---- สถานะการจัดส่งล่าสุดของบิลล่าสุด ----
    last_status: dict[str, str] = {}
    for r in con.execute("""
        SELECT o.customer_key, e.status, e.event_date
        FROM fulfilment_events e JOIN orders o ON o.order_id = e.order_id
        WHERE o.customer_key IS NOT NULL
        ORDER BY e.event_date
    """):
        last_status[r["customer_key"]] = r["status"]

    today = util.today_str()
    for c in customers:
        c["last_products"] = ", ".join(last_products.get(c["customer_key"], []))
        c["last_delivery_status"] = last_status.get(c["customer_key"], "unknown")
        try:
            import datetime as _dt
            c["days_since_last"] = (_dt.date.fromisoformat(today)
                                    - _dt.date.fromisoformat(c["last_order"])).days
        except (ValueError, TypeError):
            c["days_since_last"] = None

    base_fields = [
        "customer_key", "name", "phone", "first_order", "last_order", "days_since_last",
        "n_orders", "total_spend", "aov", "last_order_amount", "last_products",
        "last_channel", "last_team", "province", "last_delivery_status",
    ]
    p = out_dir / f"telesales_base_{as_of}.csv"
    written[p.name] = _write_csv(p, customers, base_fields)

    # ---- ข้อมูลตั้งต้นของโมเดล: ระดับออเดอร์ ไม่มีชื่อ ใช้ customer_key แทน ----
    order_rows = [dict(r) for r in con.execute("""
        SELECT order_id, customer_key, order_date, channel, team, owner, payment_method,
               amount_inc_vat, amount_ex_vat, item_count, source,
               COALESCE(is_cancelled,0) AS is_cancelled, COALESCE(is_free,0) AS is_free
        FROM orders WHERE order_date IS NOT NULL ORDER BY order_date, order_id
    """)]
    p = out_dir / f"model_orders_{as_of}.csv"
    written[p.name] = _write_csv(p, order_rows, list(order_rows[0].keys()) if order_rows else ["order_id"])

    item_rows = [dict(r) for r in con.execute("""
        SELECT i.order_id, o.customer_key, o.order_date, i.sku, i.product,
               i.qty, i.amount_inc_vat
        FROM order_items i JOIN orders o ON o.order_id = i.order_id
        WHERE o.order_date IS NOT NULL ORDER BY o.order_date, i.order_id, i.line_seq
    """)]
    p = out_dir / f"model_items_{as_of}.csv"
    written[p.name] = _write_csv(p, item_rows, list(item_rows[0].keys()) if item_rows else ["order_id"])

    return {"files": written, "customers": len(customers), "orders": len(order_rows)}
