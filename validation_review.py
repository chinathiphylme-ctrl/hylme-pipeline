# -*- coding: utf-8 -*-
"""ดูและจัดการปัญหาที่ระบบตรวจเจอ — ตัวเดียวที่ใช้เปลี่ยนสถานะ

ดูรายการ
    python validation_review.py                       ที่ต้องตรวจ (ข้อมูลปัจจุบัน · ยังไม่ได้ดู)
    python validation_review.py --summary             สรุปตัวเลขอย่างเดียว
    python validation_review.py --rule missing_sku    เจาะกฎเดียว
    python validation_review.py --scope historical    ดูข้อมูลเก่าที่เก็บไว้ใน audit
    python validation_review.py --status IGNORED      ดูที่สั่งไม่ให้เตือนแล้ว
    python validation_review.py --all                 ดูทุกสถานะทุกประเภท

เปลี่ยนสถานะ
    python validation_review.py --set REVIEWED --rule missing_sku --entity PK-4790 --note "รอ SKU"
    python validation_review.py --set IGNORED  --rule unknown_channel --scope historical --note "ต้นทางปิดแล้ว"
    python validation_review.py --set NEW      --rule missing_sku --entity PK-4790

สร้างไฟล์รายการที่ต้องแก้
    python validation_review.py --export

สถานะ
    NEW       เพิ่งเจอ ยังไม่ได้ดู           <- ตัวนี้เท่านั้นที่ขึ้นเตือนในรายงานรอบวัน
    REVIEWED  ดูแล้ว รู้แล้วว่าคืออะไร
    IGNORED   สั่งว่าไม่ต้องเตือนอีก           <- ระบบไม่ปิดให้อัตโนมัติ ไม่ว่าอะไรจะเกิดขึ้น
    RESOLVED  หายไปจากผลตรวจแล้ว (ระบบตั้งให้เอง)

ประเภทข้อมูล
    current     ต้นทางยังใช้อยู่ ตามไปแก้ได้
    historical  ต้นทางปิดไปแล้ว เก็บไว้ใน audit เฉย ๆ ไม่ขึ้นเป็นงานค้าง
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import db, report, util                         # noqa: E402
from pipeline.config import OUTPUT_DIR, get_config            # noqa: E402
from pipeline.exports import validation_export                # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="ดู/จัดการปัญหาที่ระบบตรวจเจอ")
    p.add_argument("--rule", default="", help="เจาะกฎเดียว เช่น missing_sku")
    p.add_argument("--entity", default="", help="เจาะรายการเดียว เช่น PK-4790")
    p.add_argument("--scope", default="", choices=["", "current", "historical"])
    p.add_argument("--status", default="", help="NEW | REVIEWED | RESOLVED | IGNORED")
    p.add_argument("--all", action="store_true", help="ดูทุกสถานะทุกประเภท")
    p.add_argument("--summary", action="store_true", help="แสดงแค่สรุปตัวเลข")
    p.add_argument("--limit", type=int, default=40, help="จำนวนแถวที่พิมพ์ (ค่าเริ่มต้น 40)")
    p.add_argument("--set", dest="set_status", default="",
                   help="เปลี่ยนสถานะเป็น NEW/REVIEWED/RESOLVED/IGNORED")
    p.add_argument("--note", default="", help="เหตุผล เก็บไว้ใน status_note")
    p.add_argument("--export", action="store_true", help="สร้างไฟล์ .xlsx + .csv")
    p.add_argument("--yes", action="store_true", help="ไม่ต้องถามยืนยันตอนเปลี่ยนหลายรายการ")
    return p.parse_args(argv)


def _summary(con) -> None:
    s = db.issue_summary(con)
    print("สรุปทะเบียนปัญหา\n")
    rows = [
        ("ต้องตรวจ (ข้อมูลปัจจุบัน · ยังไม่ได้ดู)", s["current_new"]),
        ("ดูแล้ว รอแก้ (REVIEWED)", s["current_reviewed"]),
        ("สั่งไม่ต้องเตือนแล้ว (IGNORED)", s["current_ignored"]),
        ("ข้อมูลเก่า เก็บใน audit อย่างเดียว", s["historical"]),
        ("ปิดไปแล้ว (RESOLVED)", s["resolved"]),
    ]
    for label, n in rows:
        print(f"  {label:<44}{n:>8,}")
    print(f"  {'-' * 52}")
    print(f"  {'รวมทั้งทะเบียน':<44}{s['total']:>8,}")


def _by_rule(con, scope: str, status: str) -> None:
    where, params = ["1=1"], []
    if scope:
        where.append("COALESCE(scope,'current')=?")
        params.append(scope)
    if status:
        where.append("status=?")
        params.append(status)
    rows = con.execute(
        f"SELECT rule, COALESCE(scope,'current') sc, status, COUNT(*) n "
        f"FROM validation_issue WHERE {' AND '.join(where)} "
        f"GROUP BY rule, sc, status ORDER BY n DESC", params).fetchall()
    if not rows:
        return
    print(f"\n{'กฎ':<28}{'ประเภท':<13}{'สถานะ':<11}{'จำนวน':>8}")
    print("-" * 62)
    for r in rows:
        print(f"{r['rule']:<28}{r['sc']:<13}{r['status']:<11}{r['n']:>8,}")


def _list(con, args) -> None:
    scope = args.scope or ("" if args.all else "current")
    status = (args.status or ("" if args.all else "NEW")).upper() if not args.all else args.status
    rows = db.list_issues(con, rule=args.rule, scope=scope, status=status,
                          limit=args.limit if not args.summary else 0)
    _by_rule(con, scope, status)
    if args.summary:
        return
    if not rows:
        print("\nไม่มีรายการตามเงื่อนไขนี้")
        return
    print(f"\n{'กฎ':<26}{'เลขที่ออเดอร์':<24}{'ต้นทาง':<22}{'วันที่':<12}"
          f"{'สถานะ':<10}{'เจอกี่รอบ':>9}")
    print("-" * 104)
    for r in rows:
        print(f"{r['rule']:<26}{str(r['entity_id'])[:22]:<24}"
              f"{str(r['entity_source'] or '')[:20]:<22}{str(r['entity_date'] or '')[:10]:<12}"
              f"{r['status']:<10}{r['seen_count']:>9,}")
    total = len(db.list_issues(con, rule=args.rule, scope=scope, status=status))
    if total > len(rows):
        print(f"\n  ...แสดง {len(rows):,} จาก {total:,} รายการ (ใช้ --limit เพื่อดูเพิ่ม)")


def main(argv=None) -> int:
    args = parse_args(argv)
    report.use_utf8_console()
    cfg = get_config()
    con = db.connect(cfg.db_path)
    bk = db.backup(cfg.db_path)          # สำรองก่อนแตะโครงสร้าง (วันละครั้ง)
    added = db.ensure_schema(con)
    print()
    if bk:
        print(f"สำรองฐานข้อมูลไว้แล้ว: {bk.name}")
    if added:
        print("เพิ่มโครงสร้างใหม่: " + ", ".join(added))
        print()

    if args.set_status:
        target = db.list_issues(con, rule=args.rule, scope=args.scope,
                                status=args.status)
        if args.entity:
            target = [r for r in target if r["entity_id"] == args.entity]
        if not target:
            print("ไม่พบรายการตามเงื่อนไขที่ระบุ — ไม่ได้เปลี่ยนอะไร")
            con.close()
            return 1
        if len(target) > 1 and not args.yes:
            print(f"จะเปลี่ยนสถานะ {len(target):,} รายการเป็น {args.set_status.upper()}")
            mix: dict[tuple, int] = {}
            for r in target:
                k = (r["rule"], r["scope"] or "current", r["status"])
                mix[k] = mix.get(k, 0) + 1
            for (rule, sc, st), n in sorted(mix.items(), key=lambda kv: -kv[1]):
                print(f"   {rule:<26}{sc:<13}{st:<10}{n:>7,}")
            print("   ตัวอย่าง: " + ", ".join(str(r["entity_id"]) for r in target[:3]))
            if input("\nยืนยันไหม (พิมพ์ y แล้ว Enter): ").strip().lower() != "y":
                print("ยกเลิก ไม่ได้เปลี่ยนอะไร")
                con.close()
                return 1
        n = db.set_issue_status(con, args.set_status, note=args.note, rule=args.rule,
                                entity_id=args.entity, scope=args.scope,
                                only_status=args.status)
        con.commit()
        print(f"เปลี่ยนสถานะแล้ว {n:,} รายการ -> {args.set_status.upper()}")
        if args.note:
            print(f"บันทึกไว้ว่า: {args.note}")
        print()
        _summary(con)
        con.close()
        return 0

    if args.export:
        ex = validation_export.write(con, OUTPUT_DIR, util.today_str())
        print(f"เขียนไฟล์แล้ว {ex['rows']:,} รายการ")
        print(f"   {ex['xlsx']}")
        print(f"   {ex['csv']}")
        con.close()
        return 0

    if args.summary:
        _summary(con)
        _by_rule(con, args.scope, args.status.upper() if args.status else "")
        con.close()
        return 0

    _summary(con)
    _list(con, args)
    print()
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
