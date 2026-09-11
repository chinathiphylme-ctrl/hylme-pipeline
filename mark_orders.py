# -*- coding: utf-8 -*-
"""ตั้งธงบนออเดอร์ด้วยมือ — ธงอยู่รอดแม้โหลดไฟล์ต้นทางทับ

ทำไมต้องมี: `daily_pipeline.py` เขียนทับตาราง orders จากไฟล์ต้นทางทุกครั้งที่โหลด
ถ้าไปแก้ `is_free` บน orders ตรง ๆ พอโหลดไฟล์เดิมซ้ำก็หายทันที
สคริปต์นี้เก็บการตัดสินใจไว้ในตาราง `order_flag` แยกต่างหาก แล้ว pipeline จะทาทับให้
หลังโหลดเสร็จทุกรอบ — หลักการเดียวกับ revenue_adjustment คือยอดดิบไม่ถูกแตะ

ตัวอย่างที่ใช้จริง — ออเดอร์ 0 บาทของแคมเปญวิ่งแลก Hylme
    python mark_orders.py --free --phones data/campaign_walk_2026-08.txt ^
           --only-zero --reason "แคมเปญวิ่งแลก Hylme ส.ค. 2026"

คำสั่งอื่น
    python mark_orders.py --list                       ดูธงทั้งหมดที่ตั้งไว้
    python mark_orders.py --free --orders PK-1234,PK-5678 --reason "ของแถม"
    python mark_orders.py --unset is_free --orders PK-1234
    python mark_orders.py --free --phones list.txt --dry-run    ดูก่อนว่าจะโดนใบไหน

ไฟล์เบอร์โทร: หนึ่งเบอร์ต่อบรรทัด หรือคั่นด้วยช่องว่าง/จุลภาคก็ได้ บรรทัดที่ขึ้นต้นด้วย # ถูกข้าม
เก็บไฟล์ไว้ใน data/ ซึ่ง .gitignore กันไม่ให้ขึ้น Git อยู่แล้ว
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import db, report, util                        # noqa: E402
from pipeline.config import get_config                       # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="ตั้งธงบนออเดอร์ด้วยมือ")
    p.add_argument("--free", action="store_true",
                   help="ทำเครื่องหมายว่าเป็นออเดอร์แจกฟรี (is_free=1)")
    p.add_argument("--unset", default="", help="ลบธงชื่อนี้ออก เช่น is_free")
    p.add_argument("--orders", default="", help="เลขที่ออเดอร์ คั่นด้วยจุลภาค")
    p.add_argument("--phones", default="", help="ไฟล์รายชื่อเบอร์โทร (หาออเดอร์จากเบอร์)")
    p.add_argument("--only-zero", action="store_true",
                   help="แตะเฉพาะออเดอร์ที่ยอดเป็น 0 บาท (กันพลาดไปแตะออเดอร์ที่มียอดจริง)")
    p.add_argument("--date-from", default="", help="จำกัดช่วงวันที่ เช่น 2026-08-01")
    p.add_argument("--date-to", default="", help="จำกัดช่วงวันที่ เช่น 2026-09-05")
    p.add_argument("--reason", default="", help="เหตุผล เก็บไว้ในตารางเพื่อตรวจย้อนหลัง")
    p.add_argument("--list", action="store_true", help="แสดงธงทั้งหมดที่ตั้งไว้")
    p.add_argument("--dry-run", action="store_true", help="ดูว่าจะโดนใบไหนบ้าง แต่ยังไม่เขียน")
    return p.parse_args(argv)


def _read_phones(path: Path) -> set[str]:
    raw = path.read_text(encoding="utf-8-sig")
    raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("#"))
    keys = set()
    for tok in re.split(r"[\s,;]+", raw):
        k = util.customer_key(tok)
        if k:
            keys.add(k)
    return keys


def _find_orders(con, args) -> list[dict]:
    where, params = ["1=1"], []
    if args.orders:
        ids = [x.strip() for x in args.orders.split(",") if x.strip()]
        where.append(f"order_id IN ({','.join('?' for _ in ids)})")
        params += ids
    if args.phones:
        keys = sorted(_read_phones(Path(args.phones)))
        if not keys:
            return []
        where.append(f"customer_key IN ({','.join('?' for _ in keys)})")
        params += keys
    if args.only_zero:
        where.append("amount_inc_vat = 0")
    if args.date_from:
        where.append("order_date >= ?")
        params.append(args.date_from)
    if args.date_to:
        where.append("order_date <= ?")
        params.append(args.date_to)
    return [dict(r) for r in con.execute(
        "SELECT order_id, order_date, source, channel, amount_inc_vat, is_free, name_raw "
        f"FROM orders WHERE {' AND '.join(where)} ORDER BY order_date, order_id", params)]


def main(argv=None) -> int:
    args = parse_args(argv)
    report.use_utf8_console()
    cfg = get_config()
    con = db.connect(cfg.db_path)
    bk = db.backup(cfg.db_path)
    db.ensure_schema(con)
    print()
    if bk:
        print(f"สำรองฐานข้อมูลไว้แล้ว: {bk.name}\n")

    if args.list:
        rows = con.execute("SELECT f.*, o.order_date, o.amount_inc_vat FROM order_flag f "
                           "LEFT JOIN orders o ON o.order_id=f.order_id "
                           "ORDER BY f.flag, f.set_at DESC, f.order_id").fetchall()
        if not rows:
            print("ยังไม่มีธงที่ตั้งไว้")
        else:
            print(f"{'ธง':<10}{'เลขที่ออเดอร์':<16}{'ค่า':<6}{'วันที่':<12}{'ยอด':>10}  เหตุผล")
            print("-" * 92)
            for r in rows:
                amt = f"{r['amount_inc_vat']:,.2f}" if r["amount_inc_vat"] is not None else "-"
                print(f"{r['flag']:<10}{str(r['order_id'])[:14]:<16}{str(r['value']):<6}"
                      f"{str(r['order_date'] or '')[:10]:<12}{amt:>10}  {r['reason'] or ''}")
            print(f"\nรวม {len(rows):,} ธง")
        con.close()
        return 0

    if not (args.free or args.unset):
        print("ต้องระบุว่าจะทำอะไร — ใช้ --free, --unset <ชื่อธง> หรือ --list")
        con.close()
        return 1
    if not (args.orders or args.phones):
        print("ต้องระบุว่าจะแตะออเดอร์ไหน — ใช้ --orders หรือ --phones")
        con.close()
        return 1

    found = _find_orders(con, args)
    if not found:
        print("ไม่พบออเดอร์ตามเงื่อนไขที่ระบุ")
        con.close()
        return 1

    flag = args.unset or "is_free"
    verb = "ลบธง" if args.unset else "ตั้งธง"
    print(f"{verb} '{flag}' กับออเดอร์ {len(found):,} ใบ")
    tot = sum(float(r["amount_inc_vat"] or 0) for r in found)
    print(f"   ยอดรวมของออเดอร์กลุ่มนี้ {tot:,.2f} บาท")
    for r in found[:8]:
        print(f"     {r['order_id']:<10}{r['order_date']}  {str(r['source'])[:10]:<12}"
              f"{str(r['channel'])[:22]:<24}{float(r['amount_inc_vat'] or 0):>9,.2f}")
    if len(found) > 8:
        print(f"     ...และอีก {len(found) - 8:,} ใบ")

    if args.dry_run:
        print("\n--dry-run: ยังไม่ได้เขียนอะไรลงฐานข้อมูล")
        con.close()
        return 0

    ids = [r["order_id"] for r in found]
    if args.unset:
        n = db.clear_order_flag(con, ids, flag)
        print(f"\nลบธงแล้ว {n:,} รายการ")
    else:
        n = db.set_order_flag(con, ids, "is_free", "1", reason=args.reason)
        print(f"\nตั้งธงแล้ว {n:,} รายการ")
        if args.reason:
            print(f"เหตุผลที่บันทึกไว้: {args.reason}")
    applied = db.apply_order_flags(con)
    con.commit()
    print(f"ทาธงลงตาราง orders แล้ว {applied['is_free']:,} แถว "
          f"(ธงทั้งหมดในระบบ {applied['total_flags']:,})")
    print("\nรัน  python daily_pipeline.py  อีกครั้งเพื่อให้รายการที่ต้องตรวจอัปเดตตาม")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
