# -*- coding: utf-8 -*-
"""ตรวจว่าไฟล์ที่จะเอาขึ้นเว็บมีข้อมูลลูกค้าหลุดไปหรือเปล่า

    python pii_scan.py                    ตรวจ dashboard.html + data/output/dashboard/*.json
    python pii_scan.py path1 path2 ...    ตรวจไฟล์/โฟลเดอร์ที่ระบุ

ตรวจสองชั้น
  ชั้นที่ 1  ค้นด้วยรูปแบบ — เบอร์โทรไทย, อีเมล, LINE ID, เลขผู้เสียภาษี 13 หลัก,
             รหัสไปรษณีย์ติดชื่อจังหวัด, เลขพัสดุ, ชื่อไทยที่ถูกปิดบัง (น******ง)
  ชั้นที่ 2  ค้นด้วยรายชื่อจริง — ดึงชื่อ/เบอร์/ที่อยู่ของลูกค้าจากฐานข้อมูลมาไล่หา
             ในไฟล์ตรง ๆ (ชั้นนี้จับได้แม้รูปแบบจะแปลกไปจากที่คาด)

ชื่อพนักงาน (ผู้ดูแลออเดอร์) ไม่ถือเป็นข้อมูลลูกค้า — แดชบอร์ดแสดงได้ตามปกติ
คืนค่า 0 = ไม่พบอะไร, 1 = พบสิ่งที่ต้องดู
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import report                                   # noqa: E402
from pipeline.config import get_config                        # noqa: E402

ROOT = Path(__file__).resolve().parent

PATTERNS = [
    ("เบอร์โทรไทย",        re.compile(r"(?<!\d)(?:0|66)[689]\d{8}(?!\d)")),
    ("เบอร์โทรมีขีด",      re.compile(r"(?<!\d)0\d{1,2}[-\s]\d{3}[-\s]\d{4}(?!\d)")),
    ("อีเมล",              re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # ต้องมีตัวคั่นและตัวไอดีจริง ๆ ตามหลัง ไม่งั้นคำว่า "LINE ID" ในคอมเมนต์จะติดมาด้วย
    ("LINE ID",            re.compile(r"(?:line\s*id|ไลน์\s*ไอดี)\s*[:：=]\s*[@A-Za-z0-9._\-]{3,}", re.I)),
    ("เลขผู้เสียภาษี",      re.compile(r"(?<!\d)\d{13}(?!\d)")),
    ("ชื่อที่ถูกปิดบัง",     re.compile(r"[฀-๿]\*{3,}[฀-๿]")),
    ("เลขพัสดุ",           re.compile(r"\b[A-Z]{2}\d{9}TH\b")),
    ("ที่อยู่ (บ้านเลขที่+ตำบล/อำเภอ)",
                           re.compile(r"\d+/\d+.{0,40}(ต\.|อ\.|ตำบล|อำเภอ|แขวง|เขต)")),
]

# คำที่ติดรูปแบบข้างบนแต่ไม่ใช่ข้อมูลลูกค้า (โดเมนของ CDN, ตัวอย่างในคอมเมนต์ ฯลฯ)
ALLOW = re.compile(
    r"cdnjs\.cloudflare\.com|w3\.org|noreply@|example\.(com|org)|@media|@keyframes|@font-face",
    re.I)


def iter_targets(args: list[str]) -> list[Path]:
    if args:
        paths = [Path(a) for a in args]
    else:
        paths = [ROOT / "dashboard.html", ROOT / "data" / "output" / "dashboard"]
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.is_file())
        elif p.is_file():
            out.append(p)
    return out


def scan_patterns(files: list[Path]) -> list[tuple]:
    hits = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            hits.append((f.name, "อ่านไฟล์ไม่ได้", str(e)))
            continue
        for label, rx in PATTERNS:
            for m in rx.finditer(text):
                frag = text[max(0, m.start()-45):m.end()+45].replace("\n", " ")
                if ALLOW.search(frag):
                    continue
                hits.append((f.name, label, frag.strip()))
    return hits


def scan_real_values(files: list[Path], con: sqlite3.Connection, limit: int = 4000) -> list[tuple]:
    """เอา 'ค่าจริง' ของลูกค้าจากฐานข้อมูลมาไล่หาในไฟล์ — ชั้นตรวจที่เชื่อได้ที่สุด"""
    def col(sql) -> list[str]:
        return [str(r[0]).strip() for r in con.execute(sql) if r[0] and str(r[0]).strip()]

    phones = set(col(f"SELECT DISTINCT phone_clean FROM orders WHERE phone_clean IS NOT NULL "
                     f"AND length(phone_clean)>=9 LIMIT {limit}"))
    phones |= set(col(f"SELECT DISTINCT phone_raw FROM orders WHERE phone_raw IS NOT NULL "
                      f"AND length(phone_raw)>=9 LIMIT {limit}"))
    names = {n for n in col(f"SELECT DISTINCT name_raw FROM orders WHERE name_raw IS NOT NULL LIMIT {limit}")
             if len(n) >= 6}
    names |= {n for n in col(f"SELECT DISTINCT recipient_name FROM orders "
                             f"WHERE recipient_name IS NOT NULL LIMIT {limit}") if len(n) >= 6}
    addrs = {a for a in col(f"SELECT DISTINCT address FROM orders WHERE address IS NOT NULL LIMIT {limit}")
             if len(a) >= 15}
    keys  = {k for k in col(f"SELECT DISTINCT customer_key FROM orders WHERE customer_key IS NOT NULL "
                            f"LIMIT {limit}") if len(k) >= 9}

    # ชื่อพนักงาน (ช่อง owner) ไม่ใช่ข้อมูลลูกค้า — แดชบอร์ดแสดงได้
    # แต่ถ้าชื่อไหนเป็นทั้งพนักงานและลูกค้า ให้แยกออกมาเตือน ไม่ใช่เงียบไป
    staff = {str(r[0]).strip() for r in con.execute(
        "SELECT DISTINCT owner FROM orders WHERE owner IS NOT NULL AND owner <> ''")}
    both = names & staff
    names = names - staff

    buckets = [("เบอร์โทรลูกค้า", phones), ("ชื่อลูกค้า", names),
               ("ที่อยู่ลูกค้า", addrs), ("รหัสลูกค้า (customer_key)", keys)]
    hits, notes = [], []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, values in buckets:
            found = [v for v in values if v in text]
            if found:
                hits.append((f.name, label, len(found), found[:5]))
        found = [v for v in both if v in text]
        if found:
            notes.append((f.name, len(found), found[:5]))
    return hits, notes, {k: len(v) for k, v in buckets}


def main(argv=None) -> int:
    report.use_utf8_console()
    args = list(argv if argv is not None else sys.argv[1:])
    files = iter_targets(args)
    print()
    print("ตรวจข้อมูลส่วนบุคคลในไฟล์ที่จะเผยแพร่")
    print("─" * 66)
    if not files:
        print("ไม่พบไฟล์ให้ตรวจ")
        return 1
    total = sum(f.stat().st_size for f in files)
    print(f"ไฟล์ที่ตรวจ {len(files)} ไฟล์  รวม {total/1024:,.0f} KB")
    for f in files:
        print(f"   {f.name:<26}{f.stat().st_size:>10,} bytes")
    print()

    bad = False
    p_hits = scan_patterns(files)
    print(f"ชั้นที่ 1 — ค้นด้วยรูปแบบ ({len(PATTERNS)} รูปแบบ)")
    if p_hits:
        bad = True
        for h in p_hits[:40]:
            print(f"   [พบ] {h[0]} · {h[1]}\n         …{h[2]}…")
        if len(p_hits) > 40:
            print(f"   ...และอีก {len(p_hits)-40} รายการ")
    else:
        print("   ไม่พบ")
    print()

    print("ชั้นที่ 2 — ค้นด้วยค่าจริงจากฐานข้อมูล")
    try:
        cfg = get_config()
        con = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
        r_hits, r_notes, counts = scan_real_values(files, con)
        con.close()
        print("   ค่าที่เอามาไล่หา: " +
              " · ".join(f"{k} {v:,}" for k, v in counts.items()))
        if r_hits:
            bad = True
            for f, label, n, sample in r_hits:
                print(f"   [พบ] {f} · {label} {n:,} ค่า  เช่น {sample}")
        else:
            print("   ไม่พบค่าของลูกค้าแม้แต่ค่าเดียวในไฟล์ที่ตรวจ")
        if r_notes:
            seen = sorted({v for _, _, s in r_notes for v in s})
            print()
            print("   หมายเหตุ: ชื่อพนักงานต่อไปนี้ปรากฏเป็น 'ชื่อลูกค้า' ในฐานข้อมูลด้วย")
            print(f"             {' · '.join(seen)}")
            print("             ไฟล์ที่เผยแพร่เก็บชื่อนี้ในฐานะ 'คนกรอกออเดอร์' เท่านั้น "
                  "ไม่ได้ผูกกับออเดอร์ของลูกค้ารายใด")
            print("             ถ้าไม่อยากให้ชื่อนี้ขึ้นเว็บ ให้ตั้งนามแฝงใน config/mappings.json")
    except Exception as e:                                     # noqa: BLE001
        print(f"   ข้ามชั้นนี้ (เปิดฐานข้อมูลไม่ได้: {e})")

    print()
    print("─" * 66)
    print("ผลตรวจ: พบสิ่งที่ต้องดู — อย่าเพิ่งเผยแพร่" if bad
          else "ผลตรวจ: ไม่พบข้อมูลลูกค้า — เผยแพร่ได้")
    print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
