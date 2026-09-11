# -*- coding: utf-8 -*-
"""รันรอบวัน — คำสั่งเดียวจบ

    python daily_pipeline.py

ทำอะไรบ้าง
    1. อ่านทุกไฟล์ใน data/raw/            (Pancake / MyCloud / ไฟล์ออเดอร์ยกเลิก)
    2. แปลงเป็นรูปแบบมาตรฐาน
    3. เขียนลง master database             (upsert — ไฟล์เดิมซ้ำไม่สร้างข้อมูลซ้ำ)
    4. จับคู่ออเดอร์ยกเลิก
    5. ตรวจความถูกต้อง
    6. สร้างไฟล์ MyCloud                    data/output/
    7. สร้างข้อมูลแดชบอร์ด                  data/output/dashboard/
    8. สร้างข้อมูลเทเลเซล + โมเดล           data/output/
    9. พิมพ์รายงาน + บันทึกลงไฟล์

ตัวเลือกเพิ่มเติม
    --dry-run          อ่านไฟล์และรายงานผล แต่ไม่เขียนอะไรลงฐานข้อมูล
    --no-archive       ไม่ย้ายไฟล์ที่โหลดแล้วไป data/archive/
    --recheck-cancels  คำนวณการจับคู่ออเดอร์ยกเลิกใหม่ทั้งหมด
    --skip-exports     ข้ามขั้นสร้างไฟล์ output (ใช้ตอนทดสอบการโหลด)
    --mc-from / --mc-to   ช่วงวันที่ของไฟล์ MyCloud (ค่าเริ่มต้น = วันที่ล่าสุดที่มีข้อมูล)
    --rebuild-engine   สั่งเครื่องคำนวณเดิม (hylme_engine/04_rebuild.py) คำนวณใหม่ต่อท้าย
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import db, report, revenue, util, validate             # noqa: E402
from pipeline.config import ARCHIVE_DIR, OUTPUT_DIR, RAW_DIR, get_config  # noqa: E402
from pipeline.exports import (conversion, dashboard, mycloud_export,          # noqa: E402
                              telesales, validation_export)
from pipeline import loaders                                        # noqa: E402
from pipeline.loaders import detect                                  # noqa: E402
from pipeline import reconcile                                       # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Hylme daily pipeline")
    p.add_argument("--dry-run", action="store_true", help="ไม่เขียนอะไรลงฐานข้อมูล")
    p.add_argument("--no-archive", action="store_true", help="ไม่ย้ายไฟล์ที่โหลดแล้ว")
    p.add_argument("--recheck-cancels", action="store_true", help="คำนวณการจับคู่ยกเลิกใหม่ทั้งหมด")
    p.add_argument("--skip-exports", action="store_true", help="ข้ามการสร้างไฟล์ output")
    p.add_argument("--skip-mc-export", action="store_true",
                   help="ไม่สร้างไฟล์ MyCloud (ใช้ในรอบอัปเดตการจัดส่ง)")
    p.add_argument("--only", help="อ่านเฉพาะไฟล์ชนิดที่ระบุ คั่นด้วยจุลภาค เช่น mycloud,cancel")
    p.add_argument("--title", default="HYLME DAILY PIPELINE", help="หัวเรื่องของรายงาน")
    p.add_argument("--validate-days", type=int, default=90,
                   help="ตรวจคุณภาพข้อมูลเฉพาะ N วันล่าสุด (0 = ตรวจทั้งฐาน) ค่าเริ่มต้น 90")
    p.add_argument("--no-reconcile", action="store_true",
                   help="ข้ามชั้นกระทบยอดรายได้ — ยอดที่แสดง = ยอดดิบจากไฟล์ต้นทาง")
    p.add_argument("--mc-from", help="วันที่เริ่มของไฟล์ MyCloud (YYYY-MM-DD)")
    p.add_argument("--mc-to", help="วันที่สิ้นสุดของไฟล์ MyCloud (YYYY-MM-DD)")
    p.add_argument("--rebuild-engine", action="store_true",
                   help="สั่ง hylme_engine/04_rebuild.py คำนวณใหม่ต่อท้าย")
    return p.parse_args(argv)


def _apply_orders(con, cfg, result, batch_id: int, file_sha: str = "",
                  run_id: int = 0) -> dict:
    """เขียนผลการอ่านไฟล์หนึ่งไฟล์ลงฐานข้อมูล"""
    counts = {"orders": 0, "updated_only": 0, "items": 0, "events": 0, "sources": 0,
              "ps_days": 0, "ps_pages": 0, "sr_days": 0, "sr_rows": 0,
              "audit": 0, "dropped": 0, "replaced_pre": 0}
    now = util.now_str()

    # ---- สถิติหน้า Pancake — คีย์คือ sha ของเนื้อไฟล์ โหลดซ้ำได้ผลเท่าเดิม ----
    if result.page_stat is not None:
        ps = db.replace_page_stat(con, file_sha, result.page_stat)
        counts["ps_days"] = ps["days"]
        counts["ps_pages"] = ps["pages"]
        return counts

    # ---- รายงานขาย Excel — upsert บน (วันที่, ช่องทาง) ----
    if result.sales_report is not None:
        sr = db.replace_sales_report(con, result.file_name, result.sales_report)
        counts["sr_days"] = sr["days"]
        counts["sr_rows"] = sr["rows"]
        return counts

    # ---- นับว่าไฟล์นี้กำลังแทนที่ข้อมูล "ที่เกลี่ยมาแล้ว" กี่ใบ (ต้องนับก่อนเขียนทับ) ----
    #      ตัวเลขนี้สำคัญ: บอกว่ายอดดิบจากไฟล์ต้นฉบับเข้าไปแทนยอดที่เคยเกลี่ยไว้แล้วกี่ใบ
    #      นับเป็น "ชุดของ order_id" กันซ้ำ เพราะใบเดียวอาจโดนทั้งสองทาง
    pre_ids: set[str] = set()
    new_ids = [o["order_id"] for o in result.orders if not o.get("_update_only")]
    for i in range(0, len(new_ids), 400):
        chunk = new_ids[i:i + 400]
        marks = ",".join("?" for _ in chunk)
        pre_ids.update(r[0] for r in con.execute(
            f"SELECT order_id FROM orders WHERE data_state='pre_reconciled' "
            f"AND order_id IN ({marks})", chunk))

    # ---- ไฟล์ที่กันซ้ำด้วย "วันที่" ไม่ใช่ order_id (Google Sheet เก่า) ----
    #      Lead ID ของไฟล์เก่าเป็นเลขลำดับที่เลื่อนได้ทุกครั้งที่แปลง จึงกันซ้ำด้วย order_id ไม่ได้
    #      ลบของเดิมเฉพาะ source + วันที่ที่ไฟล์ใหม่ครอบคลุม แล้วค่อยเขียนชุดใหม่ลงไป
    if result.replace_scope:
        scope = result.replace_scope
        dates = scope["dates"]
        if dates:
            marks = ",".join("?" for _ in dates)
            pre_ids.update(r[0] for r in con.execute(
                f"SELECT order_id FROM orders WHERE source=? AND data_state='pre_reconciled' "
                f"AND order_date IN ({marks})", [scope["source"]] + list(dates)))
        counts["dropped"] = db.drop_orders_by_scope(con, scope["source"], dates)
    counts["replaced_pre"] = len(pre_ids)

    for o in result.orders:
        if o.get("_update_only"):
            # แถวนี้เป็นข้อมูลจัดส่งของออเดอร์ที่มีอยู่แล้ว (เช่น ออเดอร์ Pancake ที่เข้า MC)
            fields = {k: v for k, v in o.items() if k not in ("_update_only", "order_id")}
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                con.execute(
                    f"UPDATE orders SET {sets} WHERE order_id=?",
                    list(fields.values()) + [o["order_id"]],
                )
                counts["updated_only"] += con.total_changes and 1 or 0
            continue
        row = dict(o)
        row["batch_id"] = batch_id
        row["loaded_from"] = result.file_name
        row["loaded_at"] = now
        db.upsert_order(con, row)
        counts["orders"] += 1

    by_order: dict[str, list[dict]] = {}
    for it in result.items:
        by_order.setdefault(it["order_id"], []).append(it)
    for order_id, items in by_order.items():
        db.replace_order_items(con, order_id, items)
        counts["items"] += len(items)

    for s in result.sources:
        db.link_source(con, s["system"], s["external_id"], s["order_id"], result.file_name)
        counts["sources"] += 1

    for e in result.events:
        if db.add_fulfilment_event(con, e["order_id"], e["event_date"], e["status"],
                                   e.get("source", ""), e.get("note", "")):
            counts["events"] += 1

    # ---- ที่มาของยอดดิบแต่ละใบ: ไฟล์ / ชีต / แถว / คอลัมน์ / การ mapping ----
    if result.audit:
        counts["audit"] = db.write_audit(con, result.audit, result.kind, result.file_name,
                                         file_sha, run_id)

    return counts


def main(argv=None) -> int:
    args = parse_args(argv)
    report.use_utf8_console()
    cfg = get_config()
    cfg.ensure_dirs()

    rep = report.Report(f"{args.title}   ·   {util.now_str()}")
    rep.header()
    started = time.time()
    only_kinds = {k.strip() for k in args.only.split(",")} if args.only else None

    # ------------------------------------------------------------ 1. ไฟล์เข้า
    rep.section("1. ไฟล์ที่พบใน data/raw/")
    files = detect.scan_inbox(RAW_DIR)
    if not files:
        rep.warn("ไม่พบไฟล์", f"วางไฟล์ .xlsx ไว้ที่ {RAW_DIR}")
        rep.footer(time.time() - started)
        rep.save(OUTPUT_DIR / f"run_report_{util.today_str()}.txt")
        return 0

    identified = []
    for f in files:
        found = detect.identify(f)
        if not found:
            rep.warn(f.name, "ไม่รู้จักรูปแบบไฟล์ — ข้ามไฟล์นี้")
        elif only_kinds and found[0] not in only_kinds:
            rep.info(f.name, f"ชนิด {found[0]} ไม่อยู่ใน --only — ทิ้งไว้ที่เดิม")
        else:
            identified.append((f, found[0], found[1]))
            rep.ok(f.name, f"ชนิด: {found[0]}  ชีต: {found[1]}")

    if not identified:
        rep.error("ไม่มีไฟล์ที่อ่านได้")
        rep.footer(time.time() - started)
        return 1

    # ------------------------------------------------------------ ฐานข้อมูล
    rep.section("2. ฐานข้อมูลหลัก")
    rep.info("ไฟล์", str(cfg.db_path))
    if args.dry_run:
        rep.warn("โหมด --dry-run", "จะไม่เขียนอะไรลงฐานข้อมูล")
    con = db.connect(cfg.db_path)
    if not args.dry_run:
        bk = db.backup(cfg.db_path)
        if bk:
            rep.ok("สำรองข้อมูลก่อนแก้", bk.name)
    added = db.ensure_schema(con)
    if added:
        rep.ok("เพิ่มตาราง/คอลัมน์ใหม่", ", ".join(added[:8]) + (" ..." if len(added) > 8 else ""))
    else:
        rep.ok("โครงสร้างฐานข้อมูลครบแล้ว")

    before = db.db_fingerprint(con)
    run_id = db.start_run(con, "daily_pipeline") if not args.dry_run else 0

    # ------------------------------------------------------------ 3. โหลด
    rep.section("3. โหลดข้อมูล")
    totals = {"orders": 0, "items": 0, "events": 0, "cancels": 0, "reloaded": 0}
    loaded_paths: list[Path] = []

    for path, kind, sheet in identified:
        try:
            result = detect.load(path, cfg)
        except Exception as exc:                       # noqa: BLE001
            rep.error(path.name, str(exc).splitlines()[0])
            continue

        st = result.stats
        if kind == "pancake":
            detail = (f"{report.fmt_int(st['orders'])} ออเดอร์ · "
                      f"{report.fmt_int(st['lines'])} บรรทัดสินค้า")
        elif kind == "mycloud":
            detail = (f"{report.fmt_int(st['orders_mc'])} ออเดอร์คีย์เอง · "
                      f"{report.fmt_int(st['orders_mkp'])} marketplace · "
                      f"{report.fmt_int(st['cancels_found'])} ยกเลิก")
        elif kind == "pagestat":
            detail = (f"{report.fmt_int(st['days'])} วัน · {st['pages']} เพจ · "
                      f"ลูกค้าใหม่ {report.fmt_int(st['new_cust'])} · "
                      f"ยอดออเดอร์ในไฟล์ {report.fmt_int(st['orders'])}")
        elif kind == "salesreport":
            detail = (f"{report.fmt_int(st['days'])} วัน ({st['from']} ถึง {st['to']}) · "
                      f"เป้ารวม {report.fmt_baht(st['total_target'])}")
        elif kind == "gosell":
            detail = (f"{report.fmt_int(st['orders'])} ออเดอร์ · "
                      f"{report.fmt_int(st['product_lines'])} บรรทัดสินค้า · "
                      f"ยกเลิก {report.fmt_int(st['cancelled'])} ใบ (ไม่โหลด)")
        elif kind == "oldsheet":
            detail = (f"{st['file_tag']} · {report.fmt_int(st['orders'])} ออเดอร์ "
                      f"จาก {st['sheets']} วัน · {report.fmt_int(st['product_lines'])} บรรทัดสินค้า")
        else:
            detail = f"{report.fmt_int(st.get('cancels_found', 0))} ออเดอร์ยกเลิก"
        rep.ok(path.name, detail)

        if not args.dry_run:
            file_sha = db.file_sha256(path)
            fresh = db.record_file(con, path, kind, result.rows_read)
            if not fresh:
                totals["reloaded"] += 1
                rep.bullet("ไฟล์นี้เคยโหลดแล้ว — ข้อมูลจะไม่เพิ่ม (upsert ทับของเดิม)")
            date_from, date_to = result.date_range
            batch_id = db.next_batch(con, kind, path.name, result.rows_read, date_from, date_to)
            counts = _apply_orders(con, cfg, result, batch_id, file_sha, run_id)
            totals["orders"] += counts["orders"]
            totals["items"] += counts["items"]
            totals["events"] += counts["events"]
            totals["ps_days"] = totals.get("ps_days", 0) + counts["ps_days"]
            totals["audit"] = totals.get("audit", 0) + counts["audit"]
            totals["replaced_pre"] = totals.get("replaced_pre", 0) + counts["replaced_pre"]
            if counts["dropped"]:
                rep.bullet(f"ลบข้อมูลเดิมของวันเดียวกัน {counts['dropped']} ใบ ก่อนเขียนชุดใหม่ "
                           f"(ไฟล์เก่ากันซ้ำด้วยวันที่ ไม่ใช่เลขที่ออเดอร์)")
            if counts["replaced_pre"]:
                rep.bullet(f"ยอดดิบเข้าแทนข้อมูลที่เคยเกลี่ยมาแล้ว {counts['replaced_pre']} ใบ "
                           f"— ใบเหล่านี้จะถูกกระทบยอดใหม่ตั้งแต่ต้น")
            if result.cancels:
                new, upd = reconcile.upsert_cancellations(con, result.cancels)
                totals["cancels"] += new
            con.commit()
            loaded_paths.append(path)

        # เตือนเรื่องคุณภาพข้อมูลที่ loader เจอ
        if kind == "pancake":
            if st.get("carried_lines"):
                rep.bullet(f"ลากรหัสคำสั่งซื้อลงมาให้ {st['carried_lines']} บรรทัด (ออเดอร์หลายสินค้า)")
            if st.get("free_orders"):
                rep.bullet(f"ออเดอร์แจกฟรี {st['free_orders']} รายการ — นับเป็นออเดอร์ ไม่นับเป็นยอดขาย")
            if st.get("used_confirm_date") == 0:
                rep.warn(path.name, "ไม่มีคอลัมน์ 'วันที่ยืนยันคำสั่งซื้อ' — ใช้วันที่สร้างแทน ยอดอาจตกผิดวัน")
            if st.get("date_shifted"):
                rep.bullet(f"{st['date_shifted']} ออเดอร์ ยืนยันคนละวันกับวันที่สร้าง")
            if st.get("unmapped_sku"):
                rep.bullet(f"{st['unmapped_sku']} บรรทัดไม่มี SKU")
        elif kind == "mycloud":
            if st.get("linked_pancake"):
                rep.bullet(f"ผูกข้อมูลจัดส่งเข้ากับออเดอร์ Pancake เดิม {st['linked_pancake']} ใบ")
            if st.get("skipped_no_key"):
                rep.bullet(f"ข้าม {st['skipped_no_key']} ออเดอร์ที่ไม่มีเลขอ้างอิง (นับจาก Pancake แล้ว)")
        elif kind == "gosell":
            if st.get("returned"):
                rep.bullet(f"ส่งคืนสำเร็จ {st['returned']} ใบ — โหลดเข้ามาแต่ไม่นับเป็นยอดขาย")
            if st.get("guessed_sku"):
                rep.bullet(f"เดา SKU จากชื่อสินค้า {st['guessed_sku']} บรรทัด (ดูรายละเอียดใน source_audit)")
            if st.get("unmapped_sku"):
                rep.bullet(f"{st['unmapped_sku']} บรรทัดยังไม่มี SKU")
            if st.get("no_date"):
                rep.warn(path.name, f"ข้าม {st['no_date']} ออเดอร์ที่ไม่มีวันที่สั่งซื้อและวันที่ชำระเงิน")
        elif kind == "oldsheet":
            if st.get("zero_value"):
                rep.bullet(f"ออเดอร์ยอด 0 บาท {st['zero_value']} ใบ — นับเป็นออเดอร์ ไม่นับเป็นยอดขาย")
            if st.get("skipped_negative"):
                rep.bullet(f"ข้าม {st['skipped_negative']} แถวที่ยอดติดลบ (คืนของ/ปรับยอด ไม่ใช่ออเดอร์ใหม่)")
            if st.get("no_items"):
                rep.bullet(f"ข้าม {st['no_items']} แถวที่แตกรายการสินค้าไม่ได้")
            if st.get("unmapped_sku"):
                rep.bullet(f"{st['unmapped_sku']} บรรทัดจับคู่ SKU ไม่ได้ — ชื่อสินค้าไม่ตรงกติกาเดิม")
            rep.bullet(f"เวลาในวันเป็นเวลาเทียม (เรียงตามลำดับแถว) เพราะไฟล์เก่าไม่ได้เก็บเวลาไว้")
        elif kind == "pagestat":
            keys = st.get("page_keys") or []
            rep.bullet("เพจในไฟล์: " + (", ".join(keys) if keys else "อ่านชื่อเพจไม่ได้"))
            if len(keys) > 1:
                rep.bullet("ไฟล์นี้มีหลายเพจ — แยก Conversion รายเพจ x รายวันไม่ได้ "
                           "ถ้าต้องการให้ export ทีละเพจจาก Pancake")
            if st.get("days") == 0:
                rep.warn(path.name, "ไม่พบชีตรายวัน (by_time) — Conversion จะคิดได้เฉพาะยอดรวมของไฟล์")

        elif kind == "salesreport":
            rep.bullet("ชีตที่อ่านได้: " + " · ".join(st.get("months") or []))
            rep.bullet(f"Upsell รวม {report.fmt_baht(st['upsell_total'])} · "
                       f"ส่งคืนสำเร็จรวม {report.fmt_baht(st['return_total'])} "
                       f"— เกลี่ยเข้าช่องทางตามสัดส่วนแล้ว")
            if st.get("formula_mismatch"):
                rep.bullet(f"{st['formula_mismatch']} วันที่สูตร L:U−I ไม่เท่ากับค่าในคอลัมน์ V "
                           f"— ระบบใช้ค่าในคอลัมน์ V เป็นหลัก (ตรงกับแดชบอร์ดเดิม)")

        if kind == "mycloud":
            unknown = st.get("unknown_status", {})
            if unknown:
                names = ", ".join(f"'{s}' ({n})" for s, n in list(unknown.items())[:6])
                rep.warn("สถานะที่ยังไม่รู้จัก", names)
                rep.bullet("ตอนนี้นับเป็นยอดขาย (เพราะไม่เข้าเงื่อนไขยกเลิก) — ถ้าถูกต้องแล้ว")
                rep.bullet('ใส่ชื่อสถานะ (ตัวพิมพ์เล็ก) ลงใน config/statuses.json คีย์ "mc_ok_status" เพื่อไม่ให้เตือนอีก')

        loaders.close_workbook()          # คืนหน่วยความจำของไฟล์ที่เพิ่งอ่านเสร็จ

    if args.dry_run:
        rep.section("จบโหมด --dry-run")
        rep.info("ไม่มีการเขียนข้อมูล")
        rep.footer(time.time() - started)
        rep.save(OUTPUT_DIR / f"run_report_{util.today_str()}.txt")
        return 0

    # ---- ทาธงที่ตั้งไว้ด้วยมือทับอีกที (ไฟล์ต้นทางเพิ่งเขียนทับ orders ไป) ----
    ch_fixed = db.apply_channel_fallback(con, cfg)
    if ch_fixed:
        rep.ok("เติมช่องทางจากผู้ดูแล",
               f"{report.fmt_int(ch_fixed)} ใบ (เฉพาะใบที่ไม่มี Lead Intro เลย · "
               f"ตั้งใน config/mappings.json -> owner_default_channel)")
    flags = db.apply_order_flags(con)
    if flags["total_flags"]:
        rep.ok("ธงที่ตั้งไว้ด้วยมือ",
               f"มีทั้งหมด {report.fmt_int(flags['total_flags'])} ธง"
               + (f" · ทาทับรอบนี้ {flags['is_free']} แถว" if flags["is_free"] else ""))
    con.commit()

    # ------------------------------------------------------------ 4. กระทบยอดยกเลิก
    rep.section("4. กระทบยอดออเดอร์ยกเลิก")
    rec = reconcile.run(con, cfg, recheck=args.recheck_cancels)
    rep.ok("จับคู่ได้", f"{report.fmt_int(rec['matched'])} รายการในรอบนี้ "
                        f"(สะสม {report.fmt_int(rec['total_in_registry'])} รายการในทะเบียน)")
    for method, n in sorted(rec["by_method"].items(), key=lambda kv: -kv[1]):
        rep.bullet(f"{method}: {n}")
    if rec.get("no_source_total"):
        rep.ok("ไม่ต้องหัก", f"{report.fmt_int(rec['no_source_total'])} ใบ — ออเดอร์ถูกยกเลิก"
                             f"ที่แพลตฟอร์มก่อนเข้าระบบ จึงไม่เคยถูกนับเป็นยอดขาย")
    if rec["still_unmatched"]:
        rep.warn("จับคู่ไม่ได้", f"{report.fmt_int(rec['still_unmatched'])} รายการ")
    if rec["still_ambiguous"]:
        rep.warn("มีผู้สมัครหลายราย", f"{report.fmt_int(rec['still_ambiguous'])} รายการ — ต้องเลือกเอง")
    for order_id, own, from_file in rec.get("amount_conflicts", [])[:5]:
        rep.bullet(f"{order_id}: ยอดในระบบ {own:,.2f} ≠ ยอดในไฟล์ยกเลิก {from_file:,.2f} (ใช้ยอดในไฟล์)")

    # ------------------------------------------------------------ 4b. กระทบยอดรายได้
    rep.section("5. กระทบยอดรายได้ (Raw -> Reported)")
    rev = revenue.rebuild(con, cfg, run_id, enabled=not args.no_reconcile)
    if not rev["enabled"]:
        rep.info("ปิดชั้นกระทบยอด (--no-reconcile)", "ยอดที่แสดง = ยอดดิบจากไฟล์ต้นทาง")
    else:
        rep.ok("ปรับแล้ว", f"{report.fmt_int(rev['adjusted'])} ออเดอร์ "
                            f"จากทั้งหมด {report.fmt_int(rev['orders'])} ใบ")
        for rule, st in sorted(rev["by_rule"].items(), key=lambda kv: -abs(kv[1]["delta"])):
            rep.bullet(f"{rule}: {report.fmt_int(st['n'])} ใบ · {report.fmt_baht(st['delta'])}")
        if rev["skipped_pre_reconciled"]:
            rep.ok("ข้ามออเดอร์ที่ผ่านการกระทบยอดมาแล้ว",
                   f"{report.fmt_int(rev['skipped_pre_reconciled'])} ใบ "
                   f"— โหลดจากไฟล์ export ของแดชบอร์ด จึงเป็นยอดหลังเกลี่ยอยู่แล้ว "
                   f"(ตั้งใจให้เป็นแบบนี้ ไม่ใช่ปัญหา)")
        if rev["calibrated_days"]:
            rep.ok("เกลี่ยตามรายงานขาย", f"{rev['calibrated_days']} วัน")
        if rev["orphan_channels"]:
            top = sorted(rev["orphan_channels"].items(), key=lambda kv: -kv[1])[:4]
            rep.bullet("ช่องทางที่รายงานมียอดแต่ไม่มีออเดอร์: "
                       + " · ".join(f"{b} {v:,.0f}" for b, v in top))
        for x in rev.get("skipped_ratio", [])[:8]:
            rep.warn("ไม่เกลี่ย เพราะยอดดิบห่างจากเป้ามากผิดปกติ",
                     f"{x['date']} {x['bucket']} · ยอดดิบ {report.fmt_baht(x['raw'])} "
                     f"vs เป้า {report.fmt_baht(x['target'])} (x{x['ratio']}, {x['orders']} ใบ) "
                     f"— น่าจะยังโหลดไฟล์ต้นทางของวันนั้นมาไม่ครบ")
        if len(rev.get("skipped_ratio", [])) > 8:
            rep.bullet(f"...และอีก {len(rev['skipped_ratio']) - 8} ช่องทาง x วัน")
        # ---- แยกของเก่าออกจากของปัจจุบัน ใช้เกณฑ์เดียวกับหัวข้อ 6 (orders.source) ----
        #      ออเดอร์จากระบบที่ปิดไปแล้วตามไปแก้ต้นทางไม่ได้ จึงไม่ควรขึ้นเป็นงานค้างทุกวัน
        #      ตรงนี้แก้แค่การแสดงผล ไม่ได้แตะวิธีคิดการเกลี่ยยอด
        lr = rev.get("large_ratio") or []
        src_of: dict[str, str] = {}
        for i in range(0, len(lr), 400):
            chunk = [x["order_id"] for x in lr[i:i + 400]]
            marks = ",".join("?" for _ in chunk)
            src_of.update({r[0]: (r[1] or "") for r in con.execute(
                f"SELECT order_id, source FROM orders WHERE order_id IN ({marks})", chunk)})
        live = [x for x in lr if src_of.get(x["order_id"], "") in cfg.live_sources]
        old = len(lr) - len(live)
        for x in live[:5]:
            rep.warn("ปรับเกิน 20%", f"{x['order_id']} {x['date']} {x['bucket']} x{x['ratio']}")
        if len(live) > 5:
            rep.bullet(f"...และอีก {len(live) - 5} ใบ")
        if old:
            rep.bullet(f"ปรับเกิน 20% อีก {old:,} ใบเป็นข้อมูลเก่า (ต้นทางปิดแล้ว) "
                       f"— เก็บใน audit อย่างเดียว ดูได้ที่ "
                       f"python validation_review.py --rule calibration_large_ratio --scope historical")
    rep.info("ยอดดิบรวมทั้งฐาน", report.fmt_baht(rev["sum_raw"]))
    rep.info("ยอดที่แสดงรวมทั้งฐาน", report.fmt_baht(rev["sum_reported"]))

    # ------------------------------------------------------------ 5. ตรวจสอบ
    rep.section("6. ตรวจความถูกต้อง")
    if args.validate_days:
        rep.info("ขอบเขต", f"ตรวจคุณภาพข้อมูล {args.validate_days} วันล่าสุด "
                            f"(ตรวจโครงสร้างทั้งฐานเสมอ · ใช้ --validate-days 0 เพื่อตรวจทั้งฐาน)")
    rep.info("การจัดกลุ่ม", "ข้อมูลปัจจุบัน = ต้นทางยังใช้อยู่ (" +
                            " · ".join(sorted(cfg.live_sources)) + ") — เตือนตามปกติ · "
                            "ข้อมูลเก่า = ต้นทางปิดแล้ว เก็บใน audit อย่างเดียว")
    findings = validate.run(con, cfg, run_id, since_days=args.validate_days)

    # แสดงเฉพาะปัญหาที่ยังลงมือแก้ได้ ส่วนข้อมูลเก่านับรวมไว้ในสรุปข้างล่าง
    for f in findings:
        if f.severity == "ok":
            rep.ok(f.rule, f.detail)
            continue
        if not f.actionable:
            continue
        if f.actionable == f.count:
            line = f.detail
        else:
            extra = [f"ข้อมูลเก่า {f.historical:,}"] if f.historical else []
            if f.muted:
                extra.append(f"จัดการไปแล้ว {f.muted:,}")
            line = (f"{f.actionable:,} รายการที่ต้องตรวจ "
                    f"(ทั้งหมด {f.count:,} · {' · '.join(extra)})")
        sample = ("  เช่น " + ", ".join(f.sample)) if f.sample else ""
        (rep.error if f.severity == "error" else rep.warn)(f.rule, line + sample)

    hidden = [f for f in findings
              if not f.actionable and (f.historical or f.muted) and f.severity != "ok"]
    if hidden:
        parts = []
        for f in hidden[:6]:
            why = []
            if f.historical:
                why.append(f"ข้อมูลเก่า {f.historical:,}")
            if f.muted:
                why.append(f"จัดการแล้ว {f.muted:,}")
            parts.append(f"{f.rule} ({' + '.join(why)})")
        rep.bullet("กฎที่ไม่มีรายการต้องทำ: " + " · ".join(parts))

    isum = db.issue_summary(con)
    rep.info("ต้องตรวจ (ข้อมูลปัจจุบัน · ยังไม่ได้ดู)", f"{report.fmt_int(isum['current_new'])} รายการ")
    rep.info("ดูแล้ว รอแก้ (REVIEWED)", f"{report.fmt_int(isum['current_reviewed'])} รายการ")
    rep.info("สั่งไม่ต้องเตือนแล้ว (IGNORED)", f"{report.fmt_int(isum['current_ignored'])} รายการ")
    rep.info("ข้อมูลเก่า เก็บใน audit อย่างเดียว", f"{report.fmt_int(isum['historical'])} รายการ")
    rep.info("ปิดไปแล้ว (RESOLVED)", f"{report.fmt_int(isum['resolved'])} รายการ")

    if not args.skip_exports:
        try:
            ex = validation_export.write(con, OUTPUT_DIR, util.today_str())
            rep.ok("ไฟล์รายการที่ต้องแก้", f"{ex['xlsx'].name} · {ex['csv'].name} "
                                            f"({report.fmt_int(ex['rows'])} รายการ)")
            if ex["by_rule"]:
                rep.bullet(" · ".join(f"{k} {v:,}" for k, v in
                                      sorted(ex["by_rule"].items(), key=lambda kv: -kv[1])))
            rep.bullet("เปลี่ยนสถานะด้วย  python validation_review.py --set REVIEWED "
                       "--rule <กฎ> --entity <เลขที่ออเดอร์> --note \"...\"")
        except Exception as exc:                                  # noqa: BLE001
            rep.warn("สร้างไฟล์รายการที่ต้องแก้ไม่ได้", str(exc))

    # ------------------------------------------------------------ 6-8. output
    if not args.skip_exports:
        if not args.skip_mc_export:
            rep.section("7. ไฟล์สำหรับ MyCloud")
            span = con.execute(
                "SELECT MIN(order_date), MAX(order_date) FROM orders WHERE order_date IS NOT NULL"
            ).fetchone()
            mc_to = args.mc_to or span[1]
            mc_from = args.mc_from or mc_to
            if mc_from and mc_to:
                built = mycloud_export.build_rows(con, cfg, mc_from, mc_to)
                if built["rows"]:
                    out = OUTPUT_DIR / f"hylme_MC_export_{mc_to}.xlsx"
                    try:
                        mycloud_export.write_xlsx(built["rows"], out)
                        rep.ok("สร้างไฟล์แล้ว",
                               f"{out.name}  ({report.fmt_int(len(built['rows']))} บรรทัด, "
                               f"ช่วง {mc_from} ถึง {mc_to})")
                    except FileNotFoundError as exc:
                        rep.error("สร้างไฟล์ MyCloud ไม่ได้", str(exc).splitlines()[0])
                else:
                    rep.info("ไม่มีออเดอร์ที่ต้อง export", f"ช่วง {mc_from} ถึง {mc_to}")
                if built["flagged"]:
                    rep.warn("ต้องตรวจก่อนอัปโหลด", f"{report.fmt_int(len(built['flagged']))} ออเดอร์")
                    for x in built["flagged"][:5]:
                        rep.bullet(f"{x['order_id']}: {x['notes']}")
                if built["skipped"]:
                    rep.warn("ถูกข้าม ไม่ได้อยู่ในไฟล์", f"{report.fmt_int(len(built['skipped']))} ออเดอร์")
                    for x in built["skipped"][:5]:
                        rep.bullet(f"{x['order_id']}: {x['reason']}")

        rep.section("8. ข้อมูลแดชบอร์ด")
        dash = dashboard.build(con, cfg, OUTPUT_DIR / "dashboard")
        rep.ok("เขียนไฟล์ JSON", f"{len(dash['files'])} ไฟล์ · {dash['days']} วัน · {dash['months']} เดือน")

        conv = conversion.build(con, cfg, OUTPUT_DIR / "dashboard")
        if conv["files_loaded"]:
            rep.ok("Conversion Rate",
                   f"จากไฟล์สถิติหน้า {conv['files_loaded']} ไฟล์ · "
                   f"{report.fmt_int(conv['days'])} วัน · {conv['months']} เดือน")
            rep.bullet("สูตร: ปิดการขาย ÷ ลูกค้าใหม่ (ไม่ใช้คอลัมน์ % ของ Pancake)")
            if conv["legacy_fallback"]:
                rep.bullet(f"ออเดอร์เก่า {report.fmt_int(conv['legacy_fallback'])} ใบ อนุมานเพจจากช่องทาง "
                           f"(แยก LINE Privilege/Official ไม่ได้)")
        else:
            rep.info("Conversion Rate", "ยังไม่มีไฟล์ pages_statistics_page_*.xlsx ในฐานข้อมูล")
        rep.bullet("ไม่มีข้อมูลส่วนบุคคลของลูกค้าในไฟล์ชุดนี้ — เอาขึ้นเว็บได้")

        rep.section("9. ข้อมูลเทเลเซล + โมเดล")
        tel = telesales.build(con, cfg, OUTPUT_DIR)
        rep.ok("เขียนไฟล์ CSV", f"ลูกค้า {report.fmt_int(tel['customers'])} ราย · "
                                f"ออเดอร์ {report.fmt_int(tel['orders'])} รายการ")
        rep.bullet("ไฟล์กลุ่มนี้มีชื่อและเบอร์โทร — เก็บไว้ในเครื่อง ห้ามขึ้น Git")

    # ------------------------------------------------------------ 9. เครื่องคำนวณเดิม
    if args.rebuild_engine:
        rep.section("10. เครื่องคำนวณคิวโทร (hylme_engine)")
        try:
            engine_dir = cfg.db_path.parent
            spec = importlib.util.spec_from_file_location("rebuild", engine_dir / "04_rebuild.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            rb, mb, n = mod.rebuild(con, util.today_str())
            rep.ok("คำนวณใหม่แล้ว", f"ลูกค้า {report.fmt_int(n)} ราย · จุดตัด R {rb} · M {mb}")
        except Exception as exc:                       # noqa: BLE001
            rep.warn("เรียกเครื่องคำนวณเดิมไม่สำเร็จ", str(exc).splitlines()[0])

    # ------------------------------------------------------------ ย้ายไฟล์
    if loaded_paths and cfg.rules.get("archive_after_load", True) and not args.no_archive:
        rep.section("11. เก็บไฟล์เข้าคลัง")
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        for p in loaded_paths:
            dest = ARCHIVE_DIR / f"{util.today_str()}_{p.name}"
            i = 1
            while dest.exists():
                dest = ARCHIVE_DIR / f"{util.today_str()}_{p.stem}({i}){p.suffix}"
                i += 1
            try:
                shutil.move(str(p), str(dest))
            except OSError as exc:
                rep.warn(p.name, f"ย้ายไม่สำเร็จ: {exc}")
        rep.ok("ย้ายไฟล์แล้ว", f"{len(loaded_paths)} ไฟล์ -> data/archive/")

    # ------------------------------------------------------------ สรุป
    after = db.db_fingerprint(con)
    rep.section("สรุปฐานข้อมูล")
    for key in ("orders", "order_items", "order_sources", "cancellations",
                "fulfilment_events", "page_stat_day", "revenue_adjustment",
                "sales_report_day"):
        delta = (after.get(key) or 0) - (before.get(key) or 0)
        sign = f"  (+{delta})" if delta > 0 else ("  (ไม่เปลี่ยน)" if delta == 0 else f"  ({delta})")
        rep.info(key, f"{report.fmt_int(after.get(key))}{sign}")
    rep.info("ยอดรวมทั้งฐาน", report.fmt_baht(after.get("sum_inc_vat")))
    if totals["reloaded"]:
        rep.ok("ไฟล์ที่โหลดซ้ำ", f"{totals['reloaded']} ไฟล์ — ข้อมูลไม่เพิ่ม (idempotent)")

    status = "error" if rep.has_error else "ok"
    db.finish_run(con, run_id, status, {
        "files": len(identified), "orders_written": totals["orders"],
        "items_written": totals["items"], "events": totals["events"],
        "cancels_new": totals["cancels"], "reconcile": {k: v for k, v in rec.items() if k != "amount_conflicts"},
        "validation": validate.summary_counts(findings),
        "before": before, "after": after,
    })
    con.close()

    rep.footer(time.time() - started)
    saved = rep.save(OUTPUT_DIR / f"run_report_{util.today_str()}.txt")
    print(f"บันทึกรายงานไว้ที่ {saved}")
    return 1 if rep.has_error else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nยกเลิกโดยผู้ใช้")
        sys.exit(130)
    except Exception:                                  # noqa: BLE001
        report.use_utf8_console()
        print("\nเกิดข้อผิดพลาดที่ไม่ได้คาดไว้:\n")
        traceback.print_exc()
        print("\nคัดลอกข้อความข้างบนทั้งหมดไปถามได้เลย")
        sys.exit(2)
