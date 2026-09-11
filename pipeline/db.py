# -*- coding: utf-8 -*-
"""ฐานข้อมูลหลัก (SQLite)

ใช้ไฟล์เดิม hylme_engine/hylme.db เป็น master database
โมดูลนี้ "เพิ่ม" ตารางและคอลัมน์ใหม่เท่านั้น ไม่ลบหรือแก้ตารางเดิม
เครื่องคำนวณเดิม (04_rebuild.py, hylme_scoring_v2_daily.py) จึงยังทำงานได้เหมือนเดิม

หลักการ idempotent
    ทุกการเขียนเป็น UPSERT บนคีย์หลัก -> โหลดไฟล์เดิมซ้ำแล้วข้อมูลไม่เพิ่ม
    fulfilment_events มี UNIQUE(order_id, event_date, status) -> INSERT OR IGNORE
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from . import util

# ---------------------------------------------------------------- ตารางพื้นฐาน
# (สร้างให้เฉพาะตอนที่ยังไม่มี — ฐานข้อมูลเดิมมีครบแล้ว)
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  order_id       TEXT PRIMARY KEY,
  source         TEXT,
  order_date     TEXT,
  order_ts       TEXT,
  channel        TEXT,
  team           TEXT,
  owner          TEXT,
  payment_method TEXT,
  phone_raw      TEXT,
  customer_key   TEXT,
  is_anonymous   INTEGER,
  name_raw       TEXT,
  amount_ex_vat  REAL,
  amount_inc_vat REAL,
  item_count     INTEGER,
  status_raw     TEXT,
  updated_at_src TEXT,
  batch_id       INTEGER);

CREATE TABLE IF NOT EXISTS order_items(
  item_id        INTEGER PRIMARY KEY,
  order_id       TEXT,
  product        TEXT,
  product_raw    TEXT,
  sku            TEXT,
  promotion      TEXT,
  qty            REAL,
  unit_ex_vat    REAL,
  discount       REAL,
  amount_ex_vat  REAL,
  amount_inc_vat REAL,
  note           TEXT);

CREATE TABLE IF NOT EXISTS fulfilment_events(
  event_id   INTEGER PRIMARY KEY,
  order_id   TEXT NOT NULL,
  event_date TEXT NOT NULL,
  status     TEXT NOT NULL,
  source     TEXT,
  note       TEXT,
  added_at   TEXT,
  UNIQUE(order_id, event_date, status));

CREATE TABLE IF NOT EXISTS src_import(
  batch_id   INTEGER PRIMARY KEY,
  source     TEXT, file_name TEXT, imported_at TEXT,
  row_count  INTEGER, date_from TEXT, date_to TEXT);

CREATE TABLE IF NOT EXISTS run_log(
  run_id INTEGER PRIMARY KEY, run_at TEXT, step TEXT, detail TEXT);

CREATE TABLE IF NOT EXISTS params(k TEXT PRIMARY KEY, v TEXT, note TEXT);
"""

# ---------------------------------------------------------------- ตารางที่เพิ่มใหม่
NEW_SCHEMA = """
-- ระบบต้นทางของออเดอร์เดียวกัน: Pancake / MyCloud / Shopee / Lazada / TikTok
-- ทำให้กฎ "ออเดอร์เดียวกันต้องเป็นออเดอร์เดียว" เป็นเรื่องของ JOIN ไม่ใช่การเดาใหม่ทุกครั้ง
CREATE TABLE IF NOT EXISTS order_sources(
  system          TEXT NOT NULL,
  external_id     TEXT NOT NULL,
  order_id        TEXT NOT NULL,
  first_seen_file TEXT,
  first_seen_at   TEXT,
  PRIMARY KEY (system, external_id));
CREATE INDEX IF NOT EXISTS ix_order_sources_order ON order_sources(order_id);

-- ทะเบียนออเดอร์ยกเลิก พร้อม "ผลการจับคู่" ที่บันทึกไว้ถาวร
-- ของเดิมคำนวณใหม่ทุกครั้ง ทำให้เคสที่เคยชัดเจนกลายเป็นกำกวมได้เมื่อมีออเดอร์ใหม่เข้ามา
CREATE TABLE IF NOT EXISTS cancellations(
  cancel_key    TEXT PRIMARY KEY,
  order_id      TEXT,
  match_status  TEXT,          -- matched | unmatched | ambiguous
  match_method  TEXT,
  matched_at    TEXT,
  confirmed_by  TEXT,          -- 'pipeline' หรือชื่อคนที่ยืนยันเอง
  source        TEXT,          -- mycloud | manual-cancel
  channel       TEXT,
  cancel_date   TEXT,
  cancel_gross  REAL,
  phone         TEXT,
  name          TEXT,
  mc_no         TEXT,
  shop_no       TEXT,
  chan_no       TEXT,
  status_raw    TEXT,
  file_name     TEXT,
  first_seen_at TEXT);
CREATE INDEX IF NOT EXISTS ix_cancel_order ON cancellations(order_id);
CREATE INDEX IF NOT EXISTS ix_cancel_date  ON cancellations(cancel_date);

-- ยอดจริงรายออเดอร์จากรายงานของแพลตฟอร์ม (แทน MKP_TRUTH ที่เคยฝังในโค้ด)
CREATE TABLE IF NOT EXISTS revenue_truth(
  system       TEXT NOT NULL,
  external_id  TEXT NOT NULL,
  settled_gross REAL,
  report_file  TEXT,
  loaded_at    TEXT,
  PRIMARY KEY (system, external_id));

-- ผลการตรวจความถูกต้องของแต่ละรอบ เก็บไว้ให้ตามงานต่อได้ ไม่หายไปกับหน้าจอ
CREATE TABLE IF NOT EXISTS validation_log(
  finding_id  INTEGER PRIMARY KEY,
  run_id      INTEGER,
  severity    TEXT,            -- ok | warning | error
  rule        TEXT,
  entity_id   TEXT,
  detail      TEXT,
  created_at  TEXT,
  resolved_at TEXT);
CREATE INDEX IF NOT EXISTS ix_validation_run ON validation_log(run_id);

-- ประวัติการรัน pipeline
CREATE TABLE IF NOT EXISTS pipeline_run(
  run_id      INTEGER PRIMARY KEY,
  command     TEXT,
  started_at  TEXT,
  finished_at TEXT,
  status      TEXT,            -- running | ok | error
  summary     TEXT);

-- ที่มาของแต่ละออเดอร์ — ตอบได้ว่ายอดดิบใบนี้มาจากไฟล์ไหน ชีตไหน แถวไหน
-- และระหว่างทางมีการ mapping อะไรบ้าง (เดา SKU จากชื่อ / แปลงช่องทาง / แปลงชื่อผู้บันทึก)
CREATE TABLE IF NOT EXISTS source_audit(
  order_id    TEXT NOT NULL,
  file_sha    TEXT NOT NULL,
  loader      TEXT,           -- gosell | oldsheet | pancake | mycloud
  file_name   TEXT,
  sheet       TEXT,
  source_row  INTEGER,        -- เลขแถวตามที่เห็นใน Excel (1-based)
  source_key  TEXT,           -- คีย์ต้นทาง เช่น เลขที่คำสั่งซื้อของ GoSell
  raw_inc_vat REAL,           -- ยอดที่อ่านได้จากไฟล์ ก่อนแปลงใด ๆ
  raw_field   TEXT,           -- ชื่อคอลัมน์ที่ยอดนั้นมาจาก
  mapping     TEXT,           -- JSON: การ mapping ที่เกิดขึ้นกับออเดอร์ใบนี้
  run_id      INTEGER,
  loaded_at   TEXT,
  PRIMARY KEY (order_id, file_sha));
CREATE INDEX IF NOT EXISTS ix_audit_order ON source_audit(order_id);
CREATE INDEX IF NOT EXISTS ix_audit_file  ON source_audit(file_sha);

-- ============================ ชั้นกระทบยอดรายได้ ============================
-- หลักการ: orders เก็บยอดดิบเท่านั้น การปรับทุกครั้งเป็นแถวที่นี่ ย้อนรอยได้ทุกบาท

-- บัญชีการปรับ — 1 แถว = 1 กฎ x 1 ออเดอร์
CREATE TABLE IF NOT EXISTS revenue_adjustment(
  adj_id        INTEGER PRIMARY KEY,
  order_id      TEXT NOT NULL,
  layer         INTEGER NOT NULL,       -- 1..4 ลำดับการปรับ
  rule          TEXT NOT NULL,          -- mkp_settled | mkp_factor | daily_calibration
                                        -- | manual_cancel_amount | manual_override
  amount_before REAL NOT NULL,          -- ยอดรวม VAT ก่อนปรับ (= ผลของชั้นก่อนหน้า)
  amount_after  REAL NOT NULL,
  delta         REAL NOT NULL,
  ratio         REAL,
  source_kind   TEXT,                   -- platform_report | sales_report_excel
                                        -- | cancel_file | operator
  source_ref    TEXT,                   -- เลขออเดอร์แพลตฟอร์ม / '2026-03-14|Shopee' / ชื่อไฟล์
  bucket        TEXT,                   -- ช่องทางที่ใช้เกลี่ย (เฉพาะ layer 3)
  run_id        INTEGER,
  computed_at   TEXT,
  note          TEXT,
  UNIQUE(order_id, layer, rule));
CREATE INDEX IF NOT EXISTS ix_adj_order ON revenue_adjustment(order_id);
CREATE INDEX IF NOT EXISTS ix_adj_rule  ON revenue_adjustment(rule);
CREATE INDEX IF NOT EXISTS ix_adj_run   ON revenue_adjustment(run_id);

-- ยอดที่ใช้แสดง — สร้างใหม่ทั้งตารางทุกรอบจาก orders + revenue_adjustment
CREATE TABLE IF NOT EXISTS order_revenue(
  order_id         TEXT PRIMARY KEY,
  order_date       TEXT,
  raw_inc_vat      REAL,
  raw_ex_vat       REAL,
  reported_inc_vat REAL,
  reported_ex_vat  REAL,
  total_ratio      REAL,
  total_delta      REAL,
  n_adjustments    INTEGER,
  rules_applied    TEXT,                -- 'mkp_settled>daily_calibration'
  is_locked        INTEGER,             -- 1 = ชั้น 1-2 แตะแล้ว ชั้น 3 ห้ามแตะ
  data_state       TEXT,                -- raw | pre_reconciled
  xl_bucket_raw    TEXT,
  xl_bucket_used   TEXT,
  run_id           INTEGER,
  computed_at      TEXT);
CREATE INDEX IF NOT EXISTS ix_orev_date   ON order_revenue(order_date);
CREATE INDEX IF NOT EXISTS ix_orev_bucket ON order_revenue(xl_bucket_used);

-- รายงานขาย Excel เป็น input ของ pipeline (แทน DAY_TARGET / DAY_CH_TARGET ที่เคยฝังในโค้ด)
CREATE TABLE IF NOT EXISTS sales_report_day(
  report_date    TEXT NOT NULL,
  channel        TEXT NOT NULL,         -- ช่องทาง หรือ '__TOTAL__'
  target_inc_vat REAL NOT NULL,
  source_file    TEXT,
  source_sheet   TEXT,
  loaded_at      TEXT,
  PRIMARY KEY (report_date, channel));

-- สรุปว่ารอบนี้แต่ละกฎทำอะไรไปบ้าง
CREATE TABLE IF NOT EXISTS revenue_rule_run(
  run_id      INTEGER NOT NULL,
  rule        TEXT NOT NULL,
  n_orders    INTEGER,
  total_delta REAL,
  date_from   TEXT,
  date_to     TEXT,
  detail      TEXT,
  PRIMARY KEY (run_id, rule));

-- สถิติหน้า Pancake (pages_statistics_page_*.xlsx) — ตัวหารของ Conversion Rate
-- เก็บระดับไฟล์ ไม่รวบยอด เพราะกติกากันนับซ้ำขึ้นกับช่วงวันที่ผู้ใช้เลือก จึงต้องรวบตอนใช้งาน
CREATE TABLE IF NOT EXISTS page_stat_file(
  file_sha    TEXT PRIMARY KEY,
  file_name   TEXT,
  page_keys   TEXT,          -- คั่นด้วย + เรียงแล้ว (index.html: setId)
  pages_json  TEXT,          -- [{id, name, key}]
  date_from   TEXT,
  date_to     TEXT,
  n_days      INTEGER,
  range_text  TEXT,
  loaded_at   TEXT);

CREATE TABLE IF NOT EXISTS page_stat_day(
  file_sha  TEXT NOT NULL,
  stat_date TEXT NOT NULL,
  new_cust  REAL, phone_all REAL, phone_new REAL,
  cmt_cust  REAL, chat_cust REAL, cmt_page  REAL, chat_page REAL,
  chat_new  REAL, chat_old  REAL, orders    REAL,
  PRIMARY KEY (file_sha, stat_date));

CREATE TABLE IF NOT EXISTS page_stat_page(
  file_sha  TEXT NOT NULL,
  page_key  TEXT NOT NULL,
  page_id   TEXT, page_name TEXT,
  new_cust  REAL, phone_all REAL, phone_new REAL,
  cmt_cust  REAL, chat_cust REAL, cmt_page  REAL, chat_page REAL,
  chat_new  REAL, chat_old  REAL, orders    REAL,
  PRIMARY KEY (file_sha, page_key));

-- ไฟล์ที่เคยโหลดแล้ว (ดูจากเนื้อไฟล์ ไม่ใช่ชื่อไฟล์) — ใช้พิสูจน์ว่ารันซ้ำแล้วไม่เพิ่มข้อมูล
CREATE TABLE IF NOT EXISTS file_manifest(
  file_sha256    TEXT PRIMARY KEY,
  file_name      TEXT,
  kind           TEXT,
  row_count      INTEGER,
  first_loaded_at TEXT,
  last_loaded_at  TEXT,
  load_count     INTEGER);

-- ทะเบียนปัญหาจากการตรวจ — หนึ่งแถวต่อ (กฎ, สิ่งที่ถูกตรวจ) ไม่โตตามจำนวนรอบที่รัน
--
-- ต่างจาก validation_log ตรงที่ log เป็นบันทึกรายรอบ (append-only ไม่มีวันลบ)
-- ส่วนตารางนี้คือ "สถานะปัจจุบัน" ของแต่ละปัญหา ทำให้ปัญหาที่ดูแล้วไม่กลับมาเตือนซ้ำ
--
-- สองแกนที่ไม่เกี่ยวกัน
--   scope   ระบบคำนวณใหม่ทุกรอบจาก orders.source — historical = ต้นทางปิดไปแล้ว แก้ย้อนหลังไม่ได้
--   status  คนตั้งเอง — ระบบไม่เคยเขียนทับ ยกเว้นปิดอัตโนมัติเมื่อปัญหาหายไปเอง
CREATE TABLE IF NOT EXISTS validation_issue(
  rule          TEXT NOT NULL,
  entity_id     TEXT NOT NULL,
  scope         TEXT,            -- historical | current   (คำนวณใหม่ทุกรอบ)
  severity      TEXT,            -- error | warning
  status        TEXT NOT NULL DEFAULT 'NEW',   -- NEW | REVIEWED | RESOLVED | IGNORED
  detail        TEXT,
  entity_source TEXT,            -- orders.source ตอนที่พบ — ไว้ตรวจย้อนว่าทำไมถูกจัดเป็นเก่า
  entity_date   TEXT,            -- orders.order_date
  first_run_id  INTEGER,
  first_seen_at TEXT,
  last_run_id   INTEGER,
  last_seen_at  TEXT,
  seen_count    INTEGER NOT NULL DEFAULT 0,
  closed_at     TEXT,            -- รอบที่ปัญหาหายไปจากผลตรวจ
  status_note   TEXT,            -- เหตุผลที่ตั้งสถานะ
  status_at     TEXT,
  PRIMARY KEY (rule, entity_id));
CREATE INDEX IF NOT EXISTS ix_issue_scope_status ON validation_issue(scope, status);
CREATE INDEX IF NOT EXISTS ix_issue_rule ON validation_issue(rule);
CREATE INDEX IF NOT EXISTS ix_issue_last_run ON validation_issue(last_run_id);

-- ธงที่ "คนเป็นคนตั้ง" บนออเดอร์ — ต้องอยู่รอดแม้โหลดไฟล์ต้นทางทับ
--
-- ทำไมต้องแยกตาราง: upsert_order() เขียนทับทุกคอลัมน์จากไฟล์ต้นทาง ถ้าไปตั้ง is_free
-- บน orders ตรง ๆ พอโหลดไฟล์เดิมซ้ำก็หายทันที ตารางนี้เก็บการตัดสินใจไว้แยก
-- แล้ว apply_order_flags() ทาทับหลังโหลดเสร็จทุกรอบ — หลักการเดียวกับ revenue_adjustment
-- คือยอดดิบไม่ถูกแตะ การตัดสินใจของคนอยู่คนละชั้น
CREATE TABLE IF NOT EXISTS order_flag(
  order_id  TEXT NOT NULL,
  flag      TEXT NOT NULL,       -- is_free | ...
  value     TEXT,
  reason    TEXT,                -- เหตุผล จะได้รู้ทีหลังว่าทำไมถึงตั้ง
  set_by    TEXT,
  set_at    TEXT,
  PRIMARY KEY (order_id, flag));
CREATE INDEX IF NOT EXISTS ix_order_flag_flag ON order_flag(flag);
"""

# คอลัมน์ที่เพิ่มเข้าไปในตาราง orders เดิม (ALTER TABLE ADD COLUMN ปลอดภัยกับข้อมูลเดิม)
ORDER_EXTRA_COLUMNS = [
    ("lead_intro", "TEXT"),        # ข้อความแหล่งที่มา ใช้แยกช่องทาง/วิธีชำระ และใช้ตอน export MC
    ("recipient_name", "TEXT"),    # ชื่อผู้รับสำหรับไฟล์ MyCloud
    ("phone_clean", "TEXT"),       # เบอร์ที่ล้างแล้ว
    ("address", "TEXT"),
    ("province", "TEXT"),
    ("postcode", "TEXT"),
    ("mc_order_no", "TEXT"),
    ("tracking_no", "TEXT"),
    ("shipping_amount", "REAL"),
    ("is_free", "INTEGER"),        # ออเดอร์แจกฟรี (ส่วนลด 100%) — นับเป็นออเดอร์ ไม่นับเป็นยอดขาย
    ("is_cancelled", "INTEGER"),
    ("cancel_gross", "REAL"),
    ("cancel_method", "TEXT"),
    ("loaded_from", "TEXT"),       # ชื่อไฟล์ที่ทำให้แถวนี้เกิด/อัปเดตล่าสุด
    ("loaded_at", "TEXT"),
    ("page_key", "TEXT"),          # เพจต้นทาง — ตัวตั้งของ Conversion (index.html: canonLeadPage)
    ("gosell_channel", "TEXT"),    # ช่องทางดิบจากไฟล์ต้นทาง ใช้ทำ xl_bucket
    ("data_state", "TEXT"),        # raw = ยอดดิบจากไฟล์ต้นทาง
                                   # pre_reconciled = ยอดที่ผ่านการกระทบยอดมาแล้ว (ห้ามเกลี่ยซ้ำ)
]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def backup(db_path: Path) -> Path | None:
    """สำเนาฐานข้อมูลไว้ก่อนแก้ครั้งแรกของวัน — กันพลาด"""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    dest = db_path.with_name(f"{db_path.stem}.backup-{util.today_str()}{db_path.suffix}")
    if not dest.exists():
        shutil.copy2(db_path, dest)
        return dest
    return None


def ensure_schema(con: sqlite3.Connection) -> list[str]:
    """สร้างตาราง/คอลัมน์ที่ยังไม่มี คืนรายการสิ่งที่เพิ่ม"""
    added: list[str] = []
    con.executescript(BASE_SCHEMA)
    con.executescript(NEW_SCHEMA)

    have = {r["name"] for r in con.execute("PRAGMA table_info(orders)")}
    for col, coltype in ORDER_EXTRA_COLUMNS:
        if col not in have:
            con.execute(f"ALTER TABLE orders ADD COLUMN {col} {coltype}")
            added.append(f"orders.{col}")

    # ---- ทำเครื่องหมายข้อมูลเก่าว่า "ผ่านการกระทบยอดมาแล้ว" ----
    # แถวที่มีอยู่ก่อน pipeline มาจากไฟล์ export ของแดชบอร์ด ซึ่งเป็นยอดหลังเกลี่ยแล้ว
    # ไม่ใช่ยอดดิบ จึงต้องกันไม่ให้ชั้นกระทบยอดไปเกลี่ยซ้ำ (พิสูจน์: ยอดรายวัน ม.ค.-มิ.ย.
    # ตรงกับ DAY_TARGET เดิม 148/181 วันแบบเป๊ะ ที่เหลือคลาดไม่เกิน 0.08 บาท)
    if "data_state" in {c for c, _ in ORDER_EXTRA_COLUMNS}:
        n = con.execute(
            "UPDATE orders SET data_state='pre_reconciled' "
            "WHERE data_state IS NULL AND loaded_at IS NULL"
        ).rowcount
        if n:
            added.append(f"orders.data_state=pre_reconciled ({n:,} แถว)")
        con.execute("UPDATE orders SET data_state='raw' WHERE data_state IS NULL")

    # ---- บันทึกไว้ใน log ด้วยว่าแต่ละรอบจัดปัญหานั้นเป็น historical หรือ current ----
    #      แถวเก่าที่บันทึกไว้ก่อนมีคอลัมน์นี้เป็น NULL — ไม่แตะ ไม่ลบ (เป็นหลักฐานย้อนหลัง)
    have_log = {r["name"] for r in con.execute("PRAGMA table_info(validation_log)")}
    if "scope" not in have_log:
        con.execute("ALTER TABLE validation_log ADD COLUMN scope TEXT")
        added.append("validation_log.scope")

    have_items = {r["name"] for r in con.execute("PRAGMA table_info(order_items)")}
    if "line_seq" not in have_items:
        con.execute("ALTER TABLE order_items ADD COLUMN line_seq INTEGER")
        added.append("order_items.line_seq")
    if "source_file" not in have_items:
        con.execute("ALTER TABLE order_items ADD COLUMN source_file TEXT")
        added.append("order_items.source_file")

    # ---- มุมมองที่แดชบอร์ดใช้ — ยอดที่แสดงแล้ว ไม่ใช่ยอดดิบ ----
    # ยังไม่ได้คำนวณชั้นกระทบยอด -> ตกกลับไปใช้ยอดดิบ (COALESCE) จึงใช้งานได้เสมอ
    con.execute("DROP VIEW IF EXISTS v_order_reported")
    con.execute("""
        CREATE VIEW v_order_reported AS
        SELECT o.*,
               COALESCE(r.reported_inc_vat, o.amount_inc_vat) AS rep_inc_vat,
               COALESCE(r.reported_ex_vat,  o.amount_ex_vat)  AS rep_ex_vat,
               COALESCE(r.raw_inc_vat,      o.amount_inc_vat) AS raw_inc_vat,
               COALESCE(r.total_ratio, 1.0)                   AS rev_ratio,
               COALESCE(r.total_delta, 0.0)                   AS rev_delta,
               r.rules_applied                                AS rev_rules,
               r.xl_bucket_used                               AS xl_bucket
        FROM orders o LEFT JOIN order_revenue r ON r.order_id = o.order_id
    """)

    # คีย์เฉพาะของบรรทัดสินค้า — ทำให้ upsert ได้และกันบรรทัดซ้ำจากการโหลดไฟล์เดิม
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_order_items_line "
        "ON order_items(order_id, line_seq)"
    )
    con.commit()
    return added


# ---------------------------------------------------------------- run tracking

def start_run(con: sqlite3.Connection, command: str) -> int:
    cur = con.execute(
        "INSERT INTO pipeline_run(command, started_at, status) VALUES (?,?,'running')",
        (command, util.now_str()),
    )
    con.commit()
    return int(cur.lastrowid)


def finish_run(con: sqlite3.Connection, run_id: int, status: str, summary: dict) -> None:
    con.execute(
        "UPDATE pipeline_run SET finished_at=?, status=?, summary=? WHERE run_id=?",
        (util.now_str(), status, json.dumps(summary, ensure_ascii=False), run_id),
    )
    con.commit()


# ---------------------------------------------------------------- file manifest

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record_file(con: sqlite3.Connection, path: Path, kind: str, row_count: int) -> bool:
    """บันทึกไฟล์ที่โหลด คืน True ถ้าเป็นไฟล์ที่ไม่เคยเห็นมาก่อน

    ไฟล์ที่เคยโหลดแล้วยังถูกประมวลผลซ้ำตามปกติ (เพราะทุกการเขียนเป็น upsert)
    แต่รายงานจะบอกว่าเป็นการโหลดซ้ำ เพื่อให้เห็นว่าข้อมูลไม่ได้เพิ่ม
    """
    sha = file_sha256(path)
    now = util.now_str()
    row = con.execute(
        "SELECT load_count FROM file_manifest WHERE file_sha256=?", (sha,)
    ).fetchone()
    if row:
        con.execute(
            "UPDATE file_manifest SET last_loaded_at=?, load_count=load_count+1, file_name=? "
            "WHERE file_sha256=?",
            (now, Path(path).name, sha),
        )
        return False
    con.execute(
        "INSERT INTO file_manifest(file_sha256, file_name, kind, row_count, "
        "first_loaded_at, last_loaded_at, load_count) VALUES (?,?,?,?,?,?,1)",
        (sha, Path(path).name, kind, row_count, now, now),
    )
    return True


def next_batch(con: sqlite3.Connection, source: str, file_name: str,
               row_count: int, date_from: str | None, date_to: str | None) -> int:
    cur = con.execute(
        "INSERT INTO src_import(source, file_name, imported_at, row_count, date_from, date_to) "
        "VALUES (?,?,?,?,?,?)",
        (source, file_name, util.now_str(), row_count, date_from, date_to),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------- upserts

ORDER_FIELDS = [
    "order_id", "source", "order_date", "order_ts", "channel", "team", "owner",
    "payment_method", "phone_raw", "customer_key", "is_anonymous", "name_raw",
    "amount_ex_vat", "amount_inc_vat", "item_count", "status_raw", "updated_at_src",
    "batch_id", "lead_intro", "recipient_name", "phone_clean", "address", "province",
    "postcode", "mc_order_no", "tracking_no", "shipping_amount", "is_free",
    "loaded_from", "loaded_at", "page_key", "gosell_channel", "data_state",
]


def upsert_order(con: sqlite3.Connection, o: dict) -> None:
    """เขียนออเดอร์ — มีอยู่แล้วให้ทับด้วยข้อมูลใหม่ ไม่สร้างแถวซ้ำ

    ตั้งใจไม่แตะ is_cancelled / cancel_gross / cancel_method ตรงนี้
    เพราะเป็นผลของขั้นตอน reconcile ไม่ใช่ของไฟล์ต้นทาง
    """
    cols = [c for c in ORDER_FIELDS if c in o]
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "order_id")
    con.execute(
        f"INSERT INTO orders({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(order_id) DO UPDATE SET {updates}",
        [o[c] for c in cols],
    )


ITEM_FIELDS = [
    "order_id", "line_seq", "product", "product_raw", "sku", "promotion", "qty",
    "unit_ex_vat", "discount", "amount_ex_vat", "amount_inc_vat", "note", "source_file",
]


def replace_order_items(con: sqlite3.Connection, order_id: str, items: list[dict]) -> None:
    """เขียนบรรทัดสินค้าของออเดอร์ใหม่ทั้งชุด

    ลบของเดิมก่อนแล้วใส่ใหม่ เพราะไฟล์ต้นทางคือความจริงเสมอ
    ทำให้โหลดไฟล์เดิมซ้ำได้ผลเท่าเดิม และไฟล์ที่แก้แล้วก็อัปเดตถูกต้อง
    """
    con.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
    if not items:
        return
    cols = ITEM_FIELDS
    placeholders = ",".join("?" for _ in cols)
    con.executemany(
        f"INSERT INTO order_items({','.join(cols)}) VALUES ({placeholders})",
        [[it.get(c) for c in cols] for it in items],
    )


def link_source(con: sqlite3.Connection, system: str, external_id: str,
                order_id: str, file_name: str) -> None:
    """ผูกเลขออเดอร์ของระบบต้นทางเข้ากับออเดอร์หลัก"""
    if not external_id:
        return
    con.execute(
        "INSERT INTO order_sources(system, external_id, order_id, first_seen_file, first_seen_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(system, external_id) DO UPDATE SET order_id=excluded.order_id",
        (system, str(external_id), order_id, file_name, util.now_str()),
    )


def add_fulfilment_event(con: sqlite3.Connection, order_id: str, event_date: str,
                         status: str, source: str, note: str = "") -> bool:
    """เพิ่มเหตุการณ์การจัดส่ง — ซ้ำแล้วข้าม (UNIQUE order_id+date+status)"""
    if not (order_id and event_date and status):
        return False
    cur = con.execute(
        "INSERT OR IGNORE INTO fulfilment_events(order_id, event_date, status, source, note, added_at) "
        "VALUES (?,?,?,?,?,?)",
        (order_id, event_date, status, source, note, util.now_str()),
    )
    return cur.rowcount > 0


PS_METRICS = ["new_cust", "phone_all", "phone_new", "cmt_cust", "chat_cust",
              "cmt_page", "chat_page", "chat_new", "chat_old", "orders"]


def replace_page_stat(con: sqlite3.Connection, file_sha: str, st: dict) -> dict:
    """เขียนสถิติหน้าของไฟล์หนึ่งไฟล์ — ลบของเดิมทั้งชุดแล้วเขียนใหม่

    คีย์คือ sha ของเนื้อไฟล์ ดังนั้นโหลดไฟล์เดิมซ้ำได้ผลเท่าเดิมเสมอ
    """
    import json as _json
    for t in ("page_stat_day", "page_stat_page"):
        con.execute(f"DELETE FROM {t} WHERE file_sha=?", (file_sha,))
    con.execute("DELETE FROM page_stat_file WHERE file_sha=?", (file_sha,))

    con.execute(
        "INSERT INTO page_stat_file(file_sha, file_name, page_keys, pages_json, "
        "date_from, date_to, n_days, range_text, loaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (file_sha, st["file"], st["set_id"], _json.dumps(st["pages"], ensure_ascii=False),
         st["from"], st["to"], len(st["date_list"]), st["range_text"], util.now_str()),
    )
    cols = ",".join(PS_METRICS)
    ph = ",".join("?" for _ in PS_METRICS)
    con.executemany(
        f"INSERT INTO page_stat_day(file_sha, stat_date, {cols}) VALUES (?,?,{ph})",
        [[file_sha, d] + [m.get(k, 0) for k in PS_METRICS] for d, m in sorted(st["by_time"].items())],
    )
    by_name = {p["key"]: p for p in st["pages"]}
    con.executemany(
        f"INSERT INTO page_stat_page(file_sha, page_key, page_id, page_name, {cols}) "
        f"VALUES (?,?,?,?,{ph})",
        [[file_sha, k, by_name.get(k, {}).get("id", ""), by_name.get(k, {}).get("name", "")]
         + [m.get(c, 0) for c in PS_METRICS]
         for k, m in sorted(st["by_page"].items())],
    )
    return {"days": len(st["by_time"]), "pages": len(st["by_page"])}


def write_audit(con: sqlite3.Connection, rows: list[dict], loader: str, file_name: str,
                file_sha: str, run_id: int) -> int:
    """บันทึกที่มาของแต่ละออเดอร์ — upsert บน (order_id, file_sha)"""
    import json as _json
    if not rows:
        return 0
    now = util.now_str()
    con.executemany(
        "INSERT INTO source_audit(order_id, file_sha, loader, file_name, sheet, source_row, "
        "source_key, raw_inc_vat, raw_field, mapping, run_id, loaded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(order_id, file_sha) DO UPDATE SET "
        "loader=excluded.loader, file_name=excluded.file_name, sheet=excluded.sheet, "
        "source_row=excluded.source_row, source_key=excluded.source_key, "
        "raw_inc_vat=excluded.raw_inc_vat, raw_field=excluded.raw_field, "
        "mapping=excluded.mapping, run_id=excluded.run_id, loaded_at=excluded.loaded_at",
        [(a["order_id"], file_sha, loader, file_name, a.get("sheet"), a.get("source_row"),
          a.get("source_key"), a.get("raw_inc_vat"), a.get("raw_field"),
          _json.dumps(a.get("mapping") or {}, ensure_ascii=False), run_id, now)
         for a in rows],
    )
    return len(rows)


def drop_orders_by_scope(con: sqlite3.Connection, source: str, dates: list[str]) -> int:
    """ลบออเดอร์ของ source นั้นในวันที่ระบุ ก่อนโหลดไฟล์ทับ

    ใช้กับไฟล์ Google Sheet เก่า ซึ่ง Lead ID เป็นเลขลำดับที่คำนวณใหม่ทุกครั้งที่แปลงไฟล์
    ถ้าจำนวนแถวเปลี่ยน เลขลำดับจะเลื่อนทั้งไฟล์ กันซ้ำด้วย order_id จึงใช้ไม่ได้
    (index.html: dropOldSheetDays บรรทัด 3298)
    """
    if not dates:
        return 0
    marks = ",".join("?" for _ in dates)
    ids = [r[0] for r in con.execute(
        f"SELECT order_id FROM orders WHERE source=? AND order_date IN ({marks})",
        [source] + list(dates))]
    if not ids:
        return 0
    idm = ",".join("?" for _ in ids)
    for t, col in (("order_items", "order_id"), ("order_sources", "order_id"),
                   ("source_audit", "order_id"), ("revenue_adjustment", "order_id"),
                   ("order_revenue", "order_id")):
        con.execute(f"DELETE FROM {t} WHERE {col} IN ({idm})", ids)
    con.execute(f"DELETE FROM orders WHERE order_id IN ({idm})", ids)
    return len(ids)


def replace_sales_report(con: sqlite3.Connection, file_name: str, rows: list[dict]) -> dict:
    """เขียนเป้ายอดขายจากรายงาน Excel — upsert บน (report_date, channel)

    ไฟล์ที่โหลดทีหลังทับไฟล์ก่อนสำหรับวันเดียวกัน (เหมือนที่ทีมออกรายงานเดือนเดิมซ้ำ)
    วันที่ที่หายไปจากไฟล์ใหม่จะไม่ถูกลบ — ต้องลบเองถ้าต้องการ
    """
    now = util.now_str()
    con.executemany(
        "INSERT INTO sales_report_day(report_date, channel, target_inc_vat, "
        "source_file, source_sheet, loaded_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(report_date, channel) DO UPDATE SET "
        "target_inc_vat=excluded.target_inc_vat, source_file=excluded.source_file, "
        "source_sheet=excluded.source_sheet, loaded_at=excluded.loaded_at",
        [(r["report_date"], r["channel"], r["target_inc_vat"], file_name,
          r.get("source_sheet"), now) for r in rows],
    )
    dates = sorted({r["report_date"] for r in rows})
    return {"rows": len(rows), "days": len(dates),
            "from": dates[0] if dates else None, "to": dates[-1] if dates else None}


def log_finding(con: sqlite3.Connection, run_id: int, severity: str,
                rule: str, entity_id: str, detail: str, scope: str = "") -> None:
    """บันทึกผลตรวจของรอบนี้ — append อย่างเดียว ไม่เคยลบ ไม่เคยแก้"""
    con.execute(
        "INSERT INTO validation_log(run_id, severity, rule, entity_id, detail, created_at, scope) "
        "VALUES (?,?,?,?,?,?,?)",
        (run_id, severity, rule, entity_id, detail, util.now_str(), scope or None),
    )


# ---------------------------------------------------------------- ธงที่คนตั้งเอง

def set_order_flag(con: sqlite3.Connection, order_ids: list[str], flag: str, value: str,
                   reason: str = "", set_by: str = "manual") -> int:
    """ตั้งธงบนออเดอร์ — เก็บแยกจาก orders เพื่อให้อยู่รอดตอนโหลดไฟล์ทับ"""
    if not order_ids:
        return 0
    now = util.now_str()
    con.executemany(
        "INSERT INTO order_flag(order_id, flag, value, reason, set_by, set_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(order_id, flag) DO UPDATE SET "
        "value=excluded.value, reason=excluded.reason, set_by=excluded.set_by, "
        "set_at=excluded.set_at",
        [(oid, flag, value, reason or None, set_by, now) for oid in order_ids])
    return len(order_ids)


def clear_order_flag(con: sqlite3.Connection, order_ids: list[str], flag: str) -> int:
    if not order_ids:
        return 0
    marks = ",".join("?" for _ in order_ids)
    cur = con.execute(f"DELETE FROM order_flag WHERE flag=? AND order_id IN ({marks})",
                      [flag] + list(order_ids))
    return cur.rowcount


def apply_order_flags(con: sqlite3.Connection) -> dict:
    """ทาธงที่คนตั้งไว้ทับลงตาราง orders — เรียกหลังโหลดไฟล์เสร็จทุกรอบ

    ไฟล์ต้นทางเขียนทับ orders ทุกครั้งที่โหลด ธงจึงต้องถูกทาใหม่หลังจากนั้นเสมอ
    """
    out = {}
    cur = con.execute(
        "UPDATE orders SET is_free = CAST((SELECT f.value FROM order_flag f "
        "  WHERE f.order_id = orders.order_id AND f.flag='is_free') AS INTEGER) "
        "WHERE EXISTS (SELECT 1 FROM order_flag f WHERE f.order_id = orders.order_id "
        "  AND f.flag='is_free') "
        "  AND COALESCE(is_free,-1) <> CAST((SELECT f.value FROM order_flag f "
        "  WHERE f.order_id = orders.order_id AND f.flag='is_free') AS INTEGER)")
    out["is_free"] = cur.rowcount
    out["total_flags"] = con.execute("SELECT COUNT(*) FROM order_flag").fetchone()[0]
    return out


def apply_channel_fallback(con: sqlite3.Connection, cfg) -> int:
    """เติมช่องทางให้ออเดอร์ที่ระบุไม่ได้ ด้วยช่องทางประจำของผู้ดูแล

    ทำไมต้องมาทำตรงนี้แทนที่จะทำตอนโหลด: ออเดอร์เก่าอยู่ในฐานไปแล้วและไฟล์ต้นทาง
    ถูกย้ายเข้า archive ไปแล้ว การแก้กติกาใน loader จึงไม่ย้อนไปแก้ของเดิม
    ขั้นนี้รันทุกรอบ ผลเหมือนเดิมทุกครั้ง (idempotent) และแตะเฉพาะใบที่
    "ไม่มี Lead Intro เลย" + "ช่องทางเป็นไม่ระบุ" เท่านั้น ใบที่มีข้อมูลอยู่แล้วไม่ถูกแตะ
    """
    mapping = getattr(cfg, "owner_default_channel", {}) or {}
    if not mapping:
        return 0
    unknown = cfg.channels["unknown_channel_label"]
    n = 0
    for owner, channel in mapping.items():
        cur = con.execute(
            "UPDATE orders SET channel=? WHERE owner=? AND channel=? "
            "AND COALESCE(TRIM(lead_intro),'')=''", (channel, owner, unknown))
        n += cur.rowcount
    return n


# ---------------------------------------------------------------- ทะเบียนปัญหา

ISSUE_STATUSES = ("NEW", "REVIEWED", "RESOLVED", "IGNORED")


def upsert_issues(con: sqlite3.Connection, rows: list[dict], run_id: int) -> dict:
    """ลงทะเบียนปัญหาที่เจอในรอบนี้

    rows: [{rule, entity_id, scope, severity, detail, entity_source, entity_date}, ...]

    กติกาที่สำคัญที่สุดของฟังก์ชันนี้
        * ปัญหาที่เพิ่งเจอครั้งแรก  -> status = NEW
        * ปัญหาที่เคยเจอแล้ว        -> อัปเดตแค่ว่าเจอล่าสุดเมื่อไร **ไม่แตะ status**
          (ถ้าเคยตั้ง REVIEWED / IGNORED ไว้ รันกี่รอบก็ยังเป็นค่าเดิม)
        * ปัญหาที่เคยปิดไปแล้ว (RESOLVED) แล้วกลับมาอีก -> กลับเป็น NEW เพราะของจริงกลับมาแล้ว
    """
    now = util.now_str()
    out = {"new": 0, "seen": 0, "reopened": 0}
    for r in rows:
        key = (r["rule"], r["entity_id"])
        cur = con.execute(
            "SELECT status FROM validation_issue WHERE rule=? AND entity_id=?", key).fetchone()
        if cur is None:
            con.execute(
                "INSERT INTO validation_issue(rule, entity_id, scope, severity, status, detail, "
                "  entity_source, entity_date, first_run_id, first_seen_at, last_run_id, "
                "  last_seen_at, seen_count) "
                "VALUES (?,?,?,?,'NEW',?,?,?,?,?,?,?,1)",
                (r["rule"], r["entity_id"], r.get("scope"), r.get("severity"), r.get("detail"),
                 r.get("entity_source"), r.get("entity_date"), run_id, now, run_id, now))
            out["new"] += 1
            continue
        reopened = cur["status"] == "RESOLVED"
        con.execute(
            "UPDATE validation_issue SET scope=?, severity=?, detail=?, entity_source=?, "
            "  entity_date=?, last_run_id=?, last_seen_at=?, seen_count=seen_count+1, "
            "  closed_at=NULL" + (", status='NEW', status_at=?, status_note=? " if reopened else " ") +
            "WHERE rule=? AND entity_id=?",
            ((r.get("scope"), r.get("severity"), r.get("detail"), r.get("entity_source"),
              r.get("entity_date"), run_id, now)
             + ((now, "กลับมาพบอีกครั้งหลังเคยปิดไปแล้ว") if reopened else ())
             + key))
        out["reopened" if reopened else "seen"] += 1
    return out


def close_missing_issues(con: sqlite3.Connection, rules: list[str], run_id: int,
                         since: str = "") -> int:
    """ปัญหาที่รอบนี้ตรวจแล้วไม่เจออีก -> ปิดเป็น RESOLVED

    กติกาที่ต้องระวัง
      * ปิดเฉพาะ NEW กับ REVIEWED — IGNORED ไม่แตะ เพราะเจ้าของสั่งไว้แล้วว่าไม่ต้องเตือน
      * ปิดเฉพาะกฎที่รอบนี้ "ได้ตรวจจริง" เท่านั้น กฎที่ไม่ได้ตรวจไม่ถูกปิดมั่ว
      * since  สำหรับกฎที่ตรวจเฉพาะ N วันล่าสุด — ปัญหาของออเดอร์ที่เก่ากว่าช่วงที่ตรวจ
               ไม่ได้ถูกมองในรอบนี้ จึงห้ามปิด (ไม่งั้นจะปิดทั้งที่ยังมีปัญหาอยู่)
    """
    if not rules:
        return 0
    rules = sorted(set(rules))
    marks = ",".join("?" for _ in rules)
    now = util.now_str()
    extra, params = "", []
    if since:
        extra = " AND (entity_date IS NULL OR entity_date = '' OR entity_date >= ?)"
        params = [since]
    cur = con.execute(
        f"UPDATE validation_issue SET status='RESOLVED', closed_at=?, status_at=?, "
        f"  status_note=TRIM(COALESCE(status_note,'') || ' | ปิดอัตโนมัติ: รอบนี้ตรวจแล้วไม่พบอีก') "
        f"WHERE rule IN ({marks}) AND status IN ('NEW','REVIEWED') "
        f"  AND COALESCE(last_run_id,-1) <> ?{extra}",
        [now, now] + rules + [run_id] + params)
    return cur.rowcount


def set_issue_status(con: sqlite3.Connection, status: str, note: str = "",
                     rule: str = "", entity_id: str = "", scope: str = "",
                     only_status: str = "") -> int:
    """เปลี่ยนสถานะปัญหา — ใช้จาก validation_review.py"""
    status = status.upper()
    if status not in ISSUE_STATUSES:
        raise ValueError(f"สถานะต้องเป็นหนึ่งใน {', '.join(ISSUE_STATUSES)}")
    where, params = ["1=1"], []
    if rule:
        where.append("rule=?")
        params.append(rule)
    if entity_id:
        where.append("entity_id=?")
        params.append(entity_id)
    if scope:
        where.append("scope=?")
        params.append(scope)
    if only_status:
        where.append("status=?")
        params.append(only_status.upper())
    cur = con.execute(
        f"UPDATE validation_issue SET status=?, status_note=?, status_at=? WHERE {' AND '.join(where)}",
        [status, note or None, util.now_str()] + params)
    return cur.rowcount


def issue_summary(con: sqlite3.Connection) -> dict:
    """นับปัญหาตาม scope x status สำหรับสรุปท้ายรายงาน"""
    out = {"current_new": 0, "current_reviewed": 0, "current_ignored": 0,
           "historical": 0, "resolved": 0, "total": 0}
    for r in con.execute(
            "SELECT COALESCE(scope,'current') s, status, COUNT(*) n "
            "FROM validation_issue GROUP BY s, status"):
        n, s, st = r["n"], r["s"], r["status"]
        out["total"] += n
        if st == "RESOLVED":
            out["resolved"] += n
        elif s == "historical":
            out["historical"] += n
        elif st == "NEW":
            out["current_new"] += n
        elif st == "REVIEWED":
            out["current_reviewed"] += n
        elif st == "IGNORED":
            out["current_ignored"] += n
    return out


def actionable_issues(con: sqlite3.Connection, rule: str = "") -> list[sqlite3.Row]:
    """ปัญหาที่ยังต้องลงมือ = ข้อมูลปัจจุบัน + ยังไม่ได้ดู"""
    sql = ("SELECT * FROM validation_issue "
           "WHERE COALESCE(scope,'current')='current' AND status='NEW'")
    params: list = []
    if rule:
        sql += " AND rule=?"
        params.append(rule)
    return con.execute(sql + " ORDER BY rule, entity_date DESC, entity_id", params).fetchall()


def list_issues(con: sqlite3.Connection, rule: str = "", scope: str = "",
                status: str = "", limit: int = 0) -> list[sqlite3.Row]:
    sql, params = "SELECT * FROM validation_issue WHERE 1=1", []
    for col, val in (("rule", rule), ("scope", scope), ("status", status.upper() if status else "")):
        if val:
            sql += f" AND {col}=?"
            params.append(val)
    sql += " ORDER BY rule, entity_date DESC, entity_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return con.execute(sql, params).fetchall()


def db_fingerprint(con: sqlite3.Connection) -> dict:
    """นับแถวของทุกตารางหลัก — ใช้พิสูจน์ว่ารันซ้ำแล้วฐานข้อมูลไม่เปลี่ยน"""
    out = {}
    for t in ("orders", "order_items", "order_sources", "cancellations",
              "fulfilment_events", "revenue_truth", "customers",
              "page_stat_file", "page_stat_day", "page_stat_page",
              "revenue_adjustment", "order_revenue", "sales_report_day"):
        try:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = None
    row = con.execute(
        "SELECT ROUND(SUM(amount_inc_vat),2), ROUND(SUM(amount_ex_vat),2) FROM orders"
    ).fetchone()
    out["sum_inc_vat"] = row[0]
    out["sum_ex_vat"] = row[1]
    return out
