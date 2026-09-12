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

SCHEMA_VERSION = 2

# ไฟล์ที่ยาวกว่านี้จะถูกเก็บแบบ "ตาราง" แทน "หนึ่งแถวหนึ่งก้อน" — ดู _as_table()
TABLE_MIN_ROWS = 400


def _round_dict(d: dict) -> dict:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def _rows_to_list(rows) -> list[dict]:
    return [_round_dict(dict(r)) for r in rows]


def _as_table(rows: list[dict]) -> dict:
    """บีบรายการแถวยาว ๆ ให้เล็กลง โดยยังอ่านออกและได้ข้อมูลเท่าเดิมทุกช่อง

    เหตุผล: ไฟล์อย่าง sets.json มี 6,000 แถว แต่ละแถวเขียนชื่อคอลัมน์ซ้ำทุกครั้ง
    และคอลัมน์ข้อความอย่าง 'ช่องทาง' / 'ผู้ดูแล' มีค่าซ้ำกันไม่กี่สิบค่า
    เก็บเป็นตาราง (ชื่อคอลัมน์ครั้งเดียว + พจนานุกรมของค่าข้อความ) เล็กลงราว 4 เท่า

    รูปแบบ:  {"format":"table", "cols":[...], "dict":{"คอลัมน์":[ค่า,...]}, "rows":[[...],...]}
    ค่าในคอลัมน์ที่อยู่ใน dict คือ "ลำดับที่" ของค่าจริงในพจนานุกรม
    ฝั่งแดชบอร์ดมีฟังก์ชัน expand() แปลงกลับเป็นแถวปกติให้เอง
    """
    cols = list(rows[0].keys())
    dicts: dict[str, list] = {}
    for c in cols:
        vals = [r.get(c) for r in rows]
        if not all(isinstance(v, str) for v in vals if v is not None):
            continue
        uniq = sorted({v for v in vals if v is not None})
        # คุ้มที่จะทำพจนานุกรมก็ต่อเมื่อค่าซ้ำกันเยอะจริง
        if uniq and len(uniq) <= max(16, len(rows) // 4):
            dicts[c] = uniq
    index = {c: {v: i for i, v in enumerate(vals)} for c, vals in dicts.items()}
    out_rows = [[index[c][r[c]] if c in index and r.get(c) is not None else r.get(c)
                 for c in cols] for r in rows]
    return {"format": "table", "cols": cols, "dict": dicts, "rows": out_rows}


def team_sql(cfg: Config, col: str = "team") -> str:
    """คืน SQL ที่แปลงค่าทีมให้เป็นคีย์เดียวกันทั้งฐาน

    ทำไมต้องมี: loader แต่ละตัวเขียนชื่อทีมคนละแบบ — ไฟล์ Pancake/R-ChatCenter เขียน
    'Admin' 'CRM' 'Telesales' ส่วนไฟล์ Google Sheet เก่ากับ GoSell เขียน 'admin' 'crm'
    'telesales' และฝั่ง Marketplace เขียน 'อื่น ๆ (Marketplace/ไม่ระบุ)' แทน 'other'
    ถ้าไม่รวมให้เป็นคีย์เดียว การ์ดทีมบนแดชบอร์ดจะแตกเป็นสองใบต่อหนึ่งทีม

    ตรงนี้แก้ที่ "ตอนสรุปออกไฟล์" เท่านั้น ไม่ได้เขียนทับค่าในตาราง orders
    (ต้นเหตุจริงอยู่ที่ loader — แยกเป็นอีกงานหนึ่ง)
    ค่าที่ไม่ตรงกับทีมไหนเลยจะถูกจัดเป็น 'other' เหมือน index.html เดิม

    สำคัญ: ทุก query ที่ใช้ตัวนี้ต้องเขียน expression ซ้ำใน GROUP BY ด้วย
    ห้ามเขียน `GROUP BY team` ลอย ๆ เพราะชื่อ team ไปชนกับคอลัมน์จริงใน v_order_reported
    แล้ว SQLite จะเลือก "คอลัมน์ต้นทาง" แทน "ชื่อผลลัพธ์" -> จัดกลุ่มด้วยค่าดิบ
    (Admin กับ admin แยกกลุ่ม) แต่พิมพ์ออกมาเป็นค่าที่รวมแล้ว ได้คีย์ซ้ำในไฟล์
    เรื่องเดียวกันกับ COALESCE(xl_bucket, channel) AS channel ใน daily_channel/items
    """
    whens = []
    for t in cfg.teams:
        for alias in dict.fromkeys([t["key"], t["name"]]):
            whens.append(f"WHEN '{str(alias).lower()}' THEN '{t['key']}'")
    return f"CASE LOWER({col}) {' '.join(whens)} ELSE 'other' END"


def build(con: sqlite3.Connection, cfg: Config, out_dir: Path) -> dict:
    """เขียนไฟล์ JSON ทั้งชุด คืนสรุปว่าเขียนอะไรไปบ้าง"""
    TEAM = team_sql(cfg, "team")
    TEAM_O = team_sql(cfg, "o.team")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}

    def dump(name: str, payload) -> None:
        path = out_dir / name
        compact = (isinstance(payload, list) and len(payload) >= TABLE_MIN_ROWS
                   and all(isinstance(x, dict) for x in payload))
        if compact:
            payload = _as_table(payload)
        with path.open("w", encoding="utf-8") as fh:
            if compact:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            else:
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
    # cancelled_orders มีไว้ให้หน้าแดชบอร์ดนับ "ออเดอร์ที่ยังอยู่" ได้ (orders - cancelled_orders)
    # ให้ตรงกับ index.html เดิมที่ตัดใบยกเลิกทิ้งก่อนคำนวณทุกการ์ด
    #
    # ช่องทาง = ช่องทางที่ใช้ตอนกระทบยอดกับรายงานขาย (xl_bucket) ถ้ามี ไม่งั้นใช้ช่องทางจากไฟล์ต้นทาง
    # ตรงกับ index.html บรรทัด 6851: const key = r.__xlBucket || channelLabel(r)
    # ถ้าใช้ channel ดิบอย่างเดียว ยอดของเดือนที่กระทบแล้วจะไปกองที่ 'ไม่ระบุช่องทาง' แทนที่จะเป็น Facebook
    daily_channel = _rows_to_list(con.execute("""
        SELECT order_date AS date, COALESCE(xl_bucket, channel) AS channel,
               COUNT(*) AS orders,
               SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 1 ELSE 0 END) AS cancelled_orders,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2) AS net
        FROM v_order_reported WHERE order_date IS NOT NULL
        GROUP BY order_date, COALESCE(xl_bucket, channel)
        ORDER BY order_date, COALESCE(xl_bucket, channel)
    """))
    dump("daily_channel.json", daily_channel)

    # ---- ยอดรายวัน x ทีม ----
    daily_team = _rows_to_list(con.execute("""
        SELECT order_date AS date, {TEAM} AS team,
               COUNT(*) AS orders,
               SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 1 ELSE 0 END) AS cancelled_orders,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2) AS net
        FROM v_order_reported WHERE order_date IS NOT NULL
        GROUP BY order_date, {TEAM}
        ORDER BY order_date, {TEAM}
    """.format(TEAM=TEAM)))
    dump("daily_team.json", daily_team)

    # ---- สินค้า (แยกตามชนิด) ----
    products = _rows_to_list(con.execute("""
        SELECT o.order_date AS date, i.product,
               ROUND(SUM(i.qty),0)             AS qty,
               ROUND(SUM(i.amount_inc_vat * o.rev_ratio),2) AS sales,
               COUNT(DISTINCT i.order_id)      AS orders
        FROM order_items i JOIN v_order_reported o ON o.order_id = i.order_id
        WHERE COALESCE(o.is_cancelled,0) = 0 AND o.order_date IS NOT NULL
        GROUP BY o.order_date, i.product ORDER BY o.order_date, i.product
    """))
    dump("products.json", products)

    # ---- ตารางรายการสินค้า (ตารางข้อเท็จจริงหลักของแดชบอร์ด) ----
    # หนึ่งแถว = วัน x ช่องทาง x วิธีจ่าย x ผู้ดูแล x ทีม x สินค้า
    # การ์ดทุกใบที่เกี่ยวกับสินค้าบนแดชบอร์ดสรุปมาจากไฟล์นี้ไฟล์เดียว จะได้ไม่มีตัวเลขสองชุดที่ขัดกันเอง
    #   - สินค้าที่ขาย (แยกตามชนิด)          -> รวมตาม product
    #   - ช่องทาง -> "ดูเพิ่มเติม" ว่าขายอะไร -> รวมตาม channel, product
    #   - COD/โอน แยกรายสินค้า               -> รวมตาม payment_method, product
    #   - จำนวนชิ้น ในตารางช่องทาง/ผู้ดูแล    -> รวมตาม channel / owner
    # orders เป็นจำนวนออเดอร์ที่ไม่ซ้ำ บวกข้ามวัน/ข้ามคอลัมน์ได้ เพราะออเดอร์หนึ่งใบ
    # อยู่ได้แค่วันเดียว ช่องทางเดียว วิธีจ่ายเดียว ผู้ดูแลคนเดียว
    items = _rows_to_list(con.execute("""
        SELECT o.order_date AS date, COALESCE(o.xl_bucket, o.channel) AS channel,
               o.payment_method AS payment, o.owner, {TEAM_O} AS team, i.product,
               ROUND(SUM(i.qty),0)                          AS units,
               ROUND(SUM(i.amount_inc_vat * o.rev_ratio),2) AS revenue,
               COUNT(DISTINCT i.order_id)                   AS orders
        FROM order_items i JOIN v_order_reported o ON o.order_id = i.order_id
        WHERE COALESCE(o.is_cancelled,0) = 0 AND o.order_date IS NOT NULL
        GROUP BY o.order_date, COALESCE(o.xl_bucket, o.channel), o.payment_method, o.owner, {TEAM_O}, i.product
        ORDER BY o.order_date, COALESCE(o.xl_bucket, o.channel), i.product
    """.format(TEAM_O=TEAM_O)))
    dump("items.json", items)

    # ---- โปรโมชั่น (สินค้า + จำนวนที่ซื้อ + ส่วนลด) ----
    # index.html: promoLabel() บรรทัด 4578 + promoAgg บรรทัด 6777
    #   ป้าย = "<ชื่อสินค้าแบบสั้น> ซื้อ <จำนวน> ชิ้น (<ประเภทราคา>)"
    #   ประเภทราคา: ส่วนลด > 0 และยอดก่อน VAT <= 0 -> แจกฟรี
    #               ส่วนลด > 0                      -> ลดพิเศษ
    #               นอกนั้น                          -> ราคาปกติ
    # แยกรายวันไว้ด้วย เพื่อให้แดชบอร์ดกรองตามเดือน/ช่วงวันได้เหมือนของเดิม
    promotions = _rows_to_list(con.execute("""
        SELECT o.order_date AS date, o.owner, {TEAM_O} AS team,
               i.product || ' ซื้อ ' || CAST(CAST(ROUND(i.qty,0) AS INTEGER) AS TEXT) || ' ชิ้น ('
                 || CASE WHEN COALESCE(i.discount,0) > 0 AND COALESCE(i.amount_ex_vat,0) <= 0
                           THEN 'แจกฟรี'
                         WHEN COALESCE(i.discount,0) > 0 THEN 'ลดพิเศษ'
                         ELSE 'ราคาปกติ' END || ')'          AS label,
               ROUND(SUM(i.qty),0)                            AS units,
               ROUND(SUM(i.amount_inc_vat * o.rev_ratio),2)   AS revenue,
               COUNT(DISTINCT i.order_id)                     AS orders
        FROM order_items i JOIN v_order_reported o ON o.order_id = i.order_id
        WHERE COALESCE(o.is_cancelled,0) = 0 AND o.order_date IS NOT NULL
        GROUP BY o.order_date, o.owner, {TEAM_O}, label ORDER BY o.order_date, label
    """.format(TEAM_O=TEAM_O)))
    dump("promotions.json", promotions)

    # ---- นับจำนวนเซ็ต (ตามมูลค่าออเดอร์หลัง VAT) ----
    # index.html: setAgg บรรทัด 6993 — ราคาที่ตายตัวมักหมายถึงเซ็ตสินค้าหนึ่งแบบ
    # นับเฉพาะออเดอร์ที่ไม่ถูกยกเลิก ใช้ยอดที่แสดง (หลังกระทบยอด) เหมือนของเดิมใช้ Lead Value
    sets = _rows_to_list(con.execute("""
        SELECT order_date AS date, owner, {TEAM} AS team,
               ROUND(rep_inc_vat,2) AS value, COUNT(*) AS count
        FROM v_order_reported
        WHERE order_date IS NOT NULL AND COALESCE(is_cancelled,0) = 0
        GROUP BY order_date, owner, {TEAM}, value ORDER BY order_date, value
    """.format(TEAM=TEAM)))
    dump("sets.json", sets)

    # ---- วิธีชำระเงิน ----
    payments = _rows_to_list(con.execute("""
        SELECT order_date AS date, payment_method,
               COUNT(*) AS orders,
               SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 1 ELSE 0 END) AS cancelled_orders,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2) AS net
        FROM v_order_reported WHERE order_date IS NOT NULL
        GROUP BY order_date, payment_method ORDER BY order_date
    """))
    dump("payments.json", payments)

    # ---- ผู้ดูแล (ชื่อพนักงาน ไม่ใช่ข้อมูลลูกค้า) ----
    owners = _rows_to_list(con.execute("""
        SELECT order_date AS date, owner, {TEAM} AS team,
               COUNT(*) AS orders,
               SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 1 ELSE 0 END) AS cancelled_orders,
               ROUND(SUM(CASE WHEN COALESCE(is_cancelled,0)=1 THEN 0 ELSE rep_inc_vat END),2) AS net
        FROM v_order_reported WHERE order_date IS NOT NULL AND owner IS NOT NULL AND owner <> ''
        GROUP BY order_date, owner, {TEAM} ORDER BY order_date
    """.format(TEAM=TEAM)))
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
        # ค่าแสดงผล (ลำดับช่องทาง สีสินค้า สีวิธีชำระเงิน) — dashboard.html อ่านจากตรงนี้
        # จะได้ไม่ต้องฝังค่าซ้ำไว้ในหน้าเว็บ แก้ที่ config/dashboard.json ที่เดียว
        "display": {**cfg.dashboard,
                    # บรรทัดที่ไม่ใช่สินค้า (ปรับเศษ/ส่วนลดระดับบิล/ค่าธรรมเนียม)
                    # แดชบอร์ดแยกไปไว้ท้ายตาราง ไม่เอาขึ้นกราฟสินค้า/โปรโมชั่น
                    # แต่ยอดเงินยังนับอยู่ครบ เพราะเป็นเงินจริงในออเดอร์
                    "non_product_labels": cfg.products.get("non_product_labels", [])},
        "note": "ไฟล์ชุดนี้เป็นตัวเลขสรุปล้วน ไม่มีข้อมูลส่วนบุคคลของลูกค้า",
    }
    dump("summary.json", summary)

    return {"files": written, "days": len(daily), "months": len(monthly)}
