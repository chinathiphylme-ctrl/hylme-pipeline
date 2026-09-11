# -*- coding: utf-8 -*-
"""รายงานว่ายอดดิบกับยอดที่แสดงต่างกันตรงไหน และเพราะกฎอะไร

    python revenue_report.py                 ทุกเดือน
    python revenue_report.py 2026-03         เจาะเดือนเดียว แยกรายวัน x ช่องทาง
    python revenue_report.py PK-4436         เจาะออเดอร์เดียว ดูทุกชั้นที่ปรับ

ไม่แก้ข้อมูลใด ๆ อ่านอย่างเดียว
"""
from __future__ import annotations

import json as _json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import db, report                      # noqa: E402
from pipeline.config import get_config               # noqa: E402

BAHT = lambda v: f"{'—':>16}" if v is None else f"{float(v):>16,.2f}"
INT = lambda v: f"{'—':>9}" if v is None else f"{int(v):>9,}"


def monthly(con) -> None:
    print("ยอดดิบ vs ยอดที่แสดง vs รายงานขาย Excel — รายเดือน\n")
    print(f"{'เดือน':<9}{'ออเดอร์':>9}{'ยอดดิบ':>16}{'ยอดที่แสดง':>16}"
          f"{'ส่วนต่าง':>14}{'รายงาน Excel':>16}{'ตรงไหม':>10}")
    print("-" * 92)
    rows = con.execute("""
        SELECT substr(o.order_date,1,7) AS m, COUNT(*) n,
               ROUND(SUM(r.raw_inc_vat),2) raw, ROUND(SUM(r.reported_inc_vat),2) rep,
               ROUND(SUM(r.total_delta),2) delta,
               SUM(CASE WHEN r.data_state='pre_reconciled' THEN 1 ELSE 0 END) pre
        FROM orders o JOIN order_revenue r ON r.order_id=o.order_id
        WHERE o.order_date IS NOT NULL GROUP BY m ORDER BY m""").fetchall()
    tg = {r["m"]: r["t"] for r in con.execute(
        "SELECT substr(report_date,1,7) m, ROUND(SUM(target_inc_vat),2) t "
        "FROM sales_report_day WHERE channel='__TOTAL__' GROUP BY m")}
    for r in rows:
        t = tg.get(r["m"])
        ok = "—" if t is None else ("ตรง" if abs(r["rep"] - t) <= 1.0 else f"{r['rep']-t:+,.0f}")
        print(f"{r['m']:<9}{INT(r['n'])}{BAHT(r['raw'])}{BAHT(r['rep'])}"
              f"{BAHT(r['delta'])}{BAHT(t)}{ok:>10}")
        if r["pre"]:
            print(f"{'':>9}{'':>7}  ({r['pre']:,} ใบเป็นข้อมูลที่ผ่านการกระทบยอดมาแล้ว จึงไม่ถูกเกลี่ยซ้ำ)")

    print("\n\nการปรับแยกตามกฎ\n")
    print(f"{'กฎ':<24}{'ออเดอร์':>9}{'ผลรวมที่ปรับ':>18}  ที่มา")
    print("-" * 72)
    for r in con.execute("""
        SELECT rule, COUNT(*) n, ROUND(SUM(delta),2) d, MIN(source_kind) k
        FROM revenue_adjustment GROUP BY rule ORDER BY ABS(SUM(delta)) DESC"""):
        print(f"{r['rule']:<24}{INT(r['n'])}{BAHT(r['d'])}  {r['k'] or ''}")
    if not con.execute("SELECT 1 FROM revenue_adjustment LIMIT 1").fetchone():
        print("  (ยังไม่มีการปรับใด ๆ — ยอดที่แสดง = ยอดดิบทั้งหมด)")


def one_month(con, month: str) -> None:
    print(f"เดือน {month} — รายวัน\n")
    print(f"{'วันที่':<12}{'ออเดอร์':>9}{'ยอดดิบ':>16}{'ยอดที่แสดง':>16}"
          f"{'เป้าในรายงาน':>16}{'ต่าง':>12}")
    print("-" * 82)
    tg = {r["d"]: r["t"] for r in con.execute(
        "SELECT report_date d, target_inc_vat t FROM sales_report_day "
        "WHERE channel='__TOTAL__' AND report_date LIKE ?", (month + "%",))}
    for r in con.execute("""
        SELECT o.order_date d, COUNT(*) n, ROUND(SUM(r.raw_inc_vat),2) raw,
               ROUND(SUM(r.reported_inc_vat),2) rep
        FROM orders o JOIN order_revenue r ON r.order_id=o.order_id
        WHERE o.order_date LIKE ? GROUP BY o.order_date ORDER BY o.order_date""", (month + "%",)):
        t = tg.get(r["d"])
        diff = "—" if t is None else f"{r['rep']-t:>+11,.2f}"
        print(f"{r['d']:<12}{INT(r['n'])}{BAHT(r['raw'])}{BAHT(r['rep'])}{BAHT(t)}{diff:>12}")

    print(f"\n\nเดือน {month} — แยกช่องทาง (ตามที่ระบบจัดให้ตอนเกลี่ย)\n")
    print(f"{'ช่องทาง':<14}{'ออเดอร์':>9}{'ยอดที่แสดง':>16}{'เป้าในรายงาน':>16}{'ต่าง':>12}")
    print("-" * 68)
    tgc = {r["c"]: r["t"] for r in con.execute(
        "SELECT channel c, ROUND(SUM(target_inc_vat),2) t FROM sales_report_day "
        "WHERE channel <> '__TOTAL__' AND report_date LIKE ? GROUP BY channel", (month + "%",))}
    seen = set()
    for r in con.execute("""
        SELECT COALESCE(r.xl_bucket_used,'(ระบุไม่ได้)') b, COUNT(*) n,
               ROUND(SUM(r.reported_inc_vat),2) rep
        FROM order_revenue r WHERE r.order_date LIKE ? GROUP BY b ORDER BY rep DESC""",
            (month + "%",)):
        t = tgc.get(r["b"])
        seen.add(r["b"])
        diff = "—" if t is None else f"{r['rep']-t:>+11,.2f}"
        print(f"{r['b']:<14}{INT(r['n'])}{BAHT(r['rep'])}{BAHT(t)}{diff:>12}")
    for c, t in sorted(tgc.items()):
        if c not in seen:
            print(f"{c:<14}{'0':>9}{BAHT(0)}{BAHT(t)}{-t:>+12,.2f}   (รายงานมียอด แต่ไม่มีออเดอร์)")


def one_order(con, order_id: str) -> None:
    o = con.execute(
        "SELECT o.order_id, o.order_date, o.source, o.channel, o.amount_inc_vat, "
        "       o.data_state, r.reported_inc_vat, r.total_ratio, r.rules_applied, "
        "       r.is_locked, r.xl_bucket_raw, r.xl_bucket_used "
        "FROM orders o LEFT JOIN order_revenue r ON r.order_id=o.order_id "
        "WHERE o.order_id=?", (order_id,)).fetchone()
    if not o:
        print(f"ไม่พบออเดอร์ {order_id}")
        return
    print(f"ออเดอร์ {o['order_id']}   ({o['order_date']} · {o['source']} · {o['channel']})\n")
    print(f"  ยอดดิบจาก source      {float(o['amount_inc_vat']):>14,.2f}")
    print(f"  ยอดที่แสดงบนแดชบอร์ด   "
          f"{(float(o['reported_inc_vat']) if o['reported_inc_vat'] is not None else 0):>14,.2f}")
    print(f"  สถานะข้อมูล            {o['data_state']}")
    print(f"  ช่องทางที่ใช้เกลี่ย      {o['xl_bucket_raw']} -> {o['xl_bucket_used']}")
    print(f"  ล็อกจากชั้น marketplace {'ใช่' if o['is_locked'] else 'ไม่'}")

    # ---- ที่มาของยอดดิบ: ไฟล์ / ชีต / แถว / คอลัมน์ / การ mapping ----
    src = con.execute(
        "SELECT loader, file_name, sheet, source_row, source_key, raw_inc_vat, raw_field, "
        "       mapping, loaded_at FROM source_audit WHERE order_id=? "
        "ORDER BY loaded_at DESC", (order_id,)).fetchall()
    if src:
        s = src[0]
        print(f"\n  ที่มาของยอดดิบ")
        print(f"    ไฟล์        {s['file_name']}" + (f"  ชีต {s['sheet']}" if s["sheet"] else ""))
        print(f"    แถวในไฟล์   {s['source_row']}   (คีย์ {s['source_key']})")
        print(f"    ยอดจากไฟล์  {float(s['raw_inc_vat']):,.2f}   จาก {s['raw_field']}")
        print(f"    โหลดเมื่อ    {s['loaded_at']}   โดย loader {s['loader']}")
        try:
            m = _json.loads(s["mapping"] or "{}")
        except ValueError:
            m = {}
        for k, v in m.items():
            if v in (None, "", [], {}, False):
                continue
            txt = _json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
            print(f"    {k:<22}{txt[:96]}")
        if len(src) > 1:
            print(f"    (ออเดอร์นี้พบในไฟล์ต้นทาง {len(src)} ไฟล์ — แสดงไฟล์ล่าสุด)")
    else:
        print(f"\n  ที่มาของยอดดิบ  ไม่มีบันทึก — เป็นข้อมูลที่โหลดมาก่อนมี pipeline")

    rows = con.execute(
        "SELECT layer, rule, amount_before, amount_after, delta, ratio, source_kind, "
        "       source_ref, bucket, note, computed_at FROM revenue_adjustment "
        "WHERE order_id=? ORDER BY layer", (order_id,)).fetchall()
    if not rows:
        print("\n  ไม่มีการปรับ — ยอดที่แสดง = ยอดดิบ")
        return
    print(f"\n  {'ชั้น':<5}{'กฎ':<22}{'ก่อน':>13}{'หลัง':>13}{'ต่าง':>12}{'อัตรา':>9}  หลักฐาน")
    print("  " + "-" * 96)
    for r in rows:
        ratio = "—" if r["ratio"] is None else f"{r['ratio']:.4f}"
        print(f"  {r['layer']:<5}{r['rule']:<22}{r['amount_before']:>13,.2f}"
              f"{r['amount_after']:>13,.2f}{r['delta']:>+12,.2f}{ratio:>9}  "
              f"{r['source_kind']}: {r['source_ref']}")
        if r["note"]:
            print(f"        {r['note']}")


def main(argv: list[str]) -> int:
    report.use_utf8_console()
    cfg = get_config()
    con = db.connect(cfg.db_path)
    arg = argv[1] if len(argv) > 1 else ""
    print()
    print("=" * 92)
    if re.fullmatch(r"\d{4}-\d{2}", arg):
        one_month(con, arg)
    elif arg:
        one_order(con, arg)
    else:
        monthly(con)
    print("=" * 92)
    print()
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
