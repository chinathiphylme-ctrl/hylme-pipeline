# Schema ที่จะเพิ่ม — ชั้นกระทบยอดรายได้ (Revenue Reconciliation Layer)

เอกสารเสนอ schema เพื่อขออนุมัติ **ยังไม่ได้แก้โค้ด**

หลักการที่ยึด: `orders` เก็บยอดดิบเท่านั้น การปรับทุกครั้งเป็น "แถวในบัญชีปรับปรุง"
ยอดที่แสดงบนแดชบอร์ดเป็นผลลัพธ์ที่คำนวณจากสองอย่างนี้ ไม่ใช่ค่าที่เขียนทับกันไปมา

```
Raw source ──► orders (ยอดดิบ ไม่ถูกแตะ)
                 │
                 ├──► revenue_adjustment  (ปรับด้วยกฎอะไร จากหลักฐานไหน เท่าไร)
                 │
                 └──► order_revenue       (ยอดที่ใช้แสดง = ดิบ + ผลรวมการปรับ)
                          │
                          └──► Dashboard
```

---

## 0 · เรื่องที่ต้องแก้ก่อน เพราะขัดกับหลักการนี้

โค้ดที่ส่งไปแล้วมีอยู่จุดหนึ่งที่ **เขียนทับยอดดิบ** — ขัดกับสิ่งที่ตกลงกันรอบนี้

`pipeline/reconcile.py` เมื่อไฟล์ออเดอร์ยกเลิกของ Hylme ระบุยอดเงินไม่ตรงกับยอดในระบบ
ปัจจุบันสั่ง `UPDATE orders SET amount_inc_vat = <ยอดในไฟล์>` (ตามพฤติกรรมเดิมของ `index.html` บรรทัด 3930)

ในดีไซน์ใหม่จะย้ายมาเป็นแถวใน `revenue_adjustment` ด้วยกฎ `manual_cancel_amount`
ยอดดิบใน `orders` จะไม่ถูกแตะ และค่า `manual_cancel_overrides_amount` ใน `config/rules.json` จะถูกลบทิ้ง
(พฤติกรรมที่เห็นบนแดชบอร์ดเหมือนเดิม แต่กลายเป็นสิ่งที่ย้อนรอยได้)

---

## 1 · ชั้นของการปรับ (Layer)

ลำดับสำคัญ — ผลของชั้นก่อนหน้าเป็นตัวตั้งของชั้นถัดไป

| Layer | รหัสกฎ | ปรับอะไร | หลักฐานมาจากไหน | ขอบเขต |
|---:|---|---|---|---|
| 0 | — | ยอดดิบ | ไฟล์ Pancake / MyCloud / Marketplace | ทุกออเดอร์ |
| 1 | `mkp_settled` | เขียนทับด้วยยอดที่แพลตฟอร์มจ่ายจริง | `revenue_truth` (รายงาน Shopee / Lazada / TikTok) | ออเดอร์ marketplace เดือน ≤ `mkp_fix_until` |
| 2 | `mkp_factor` | คูณด้วยตัวคูณรายเดือน (ใช้เมื่อไม่มียอดจริงราย order) | `config/revenue_rules.json → mkp_factor` | เหมือน layer 1 |
| 3 | `daily_calibration` | เกลี่ยให้ยอดรายวัน × ช่องทาง ตรงรายงานขาย Excel | `sales_report_day` | ออเดอร์ที่ **ไม่ถูกล็อก** จาก layer 1–2 และมีเป้าของวันนั้น |
| 4 | `manual_cancel_amount` | ใช้ยอดที่ไฟล์ยกเลิกระบุมาตรง ๆ | ไฟล์ "ออเดอร์ยกเลิก / ไม่ได้จัดส่ง" | ออเดอร์ที่จับคู่ยกเลิกได้ |
| 4 | `manual_override` | คนแก้เอง | ผู้ใช้ | เฉพาะที่กรอก |

**Lock** — ออเดอร์ที่ผ่าน layer 1 หรือ 2 แล้วจะถูกทำเครื่องหมายล็อก layer 3 จะไม่แตะ
(ตรงกับ `locked` ใน `index.html` บรรทัด 6567)

**ขอบเขตที่ต้องรู้** — `DAY_TARGET` เดิมมีข้อมูล **2026-01-01 ถึง 2026-06-30 เท่านั้น (181 วัน)**
ตั้งแต่ ก.ค. เป็นต้นไปไม่มีการเกลี่ย เพราะทีมคีย์ข้อมูลเองแล้วซึ่งเป็นยอดจริงอยู่แล้ว
แปลว่าความต่างระหว่าง pipeline กับแดชบอร์ดเดิมจำกัดอยู่แค่ ม.ค.–มิ.ย. เท่านั้น

---

## 2 · ตารางที่เพิ่ม

### 2.1 `revenue_adjustment` — บัญชีการปรับ (หัวใจของทั้งหมด)

หนึ่งแถว = การปรับหนึ่งครั้ง ด้วยกฎหนึ่งกฎ ต่อออเดอร์หนึ่งใบ

```sql
CREATE TABLE revenue_adjustment(
  adj_id        INTEGER PRIMARY KEY,
  order_id      TEXT NOT NULL,          -- -> orders.order_id
  layer         INTEGER NOT NULL,       -- 1..4 ลำดับการปรับ
  rule          TEXT NOT NULL,          -- mkp_settled | mkp_factor | daily_calibration
                                        -- | manual_cancel_amount | manual_override
  amount_before REAL NOT NULL,          -- ยอดรวม VAT ก่อนปรับ (= ผลของ layer ก่อนหน้า)
  amount_after  REAL NOT NULL,          -- ยอดรวม VAT หลังปรับ
  delta         REAL NOT NULL,          -- amount_after - amount_before
  ratio         REAL,                   -- amount_after / amount_before (NULL ถ้า before = 0)
  source_kind   TEXT,                   -- platform_report | sales_report_excel
                                        -- | cancel_file | operator
  source_ref    TEXT,                   -- เลขออเดอร์แพลตฟอร์ม / '2026-03-14|Shopee' / ชื่อไฟล์
  bucket        TEXT,                   -- ช่องทางที่ใช้เกลี่ย (เฉพาะ layer 3)
  run_id        INTEGER,                -- -> pipeline_run.run_id
  computed_at   TEXT,
  note          TEXT,
  UNIQUE(order_id, layer, rule));
CREATE INDEX ix_adj_order ON revenue_adjustment(order_id);
CREATE INDEX ix_adj_rule  ON revenue_adjustment(rule);
CREATE INDEX ix_adj_run   ON revenue_adjustment(run_id);
```

`UNIQUE(order_id, layer, rule)` ทำให้ **รันซ้ำแล้วไม่เกิดแถวซ้ำ** — upsert ทับของเดิม
ส่วน `manual_override` ที่คนกรอกเองจะไม่ถูกลบตอนคำนวณใหม่ (ดู §4)

### 2.2 `order_revenue` — ยอดที่ใช้แสดง (คำนวณใหม่ทุกรอบ)

```sql
CREATE TABLE order_revenue(
  order_id         TEXT PRIMARY KEY,    -- -> orders.order_id
  raw_inc_vat      REAL,                -- คัดลอกจาก orders เพื่อเทียบง่าย
  raw_ex_vat       REAL,
  reported_inc_vat REAL,                -- ยอดที่แดชบอร์ดใช้
  reported_ex_vat  REAL,
  total_ratio      REAL,                -- reported / raw  (ใช้ปรับยอดรายสินค้าด้วย)
  total_delta      REAL,
  n_adjustments    INTEGER,
  rules_applied    TEXT,                -- 'mkp_settled>daily_calibration'
  is_locked        INTEGER,             -- 1 = layer 1-2 แตะแล้ว layer 3 ห้ามแตะ
  xl_bucket_raw    TEXT,                -- ช่องทางที่อ่านได้จากตัวออเดอร์เอง
  xl_bucket_used   TEXT,                -- ช่องทางที่ถูกโยนเข้าไปตอนเกลี่ย (อาจต่างจาก raw)
  run_id           INTEGER,
  computed_at      TEXT);
CREATE INDEX ix_orev_bucket ON order_revenue(xl_bucket_used);
```

> **ทำไมไม่ทำเป็น VIEW** — เพราะ layer 3 ต้องเกลี่ยเป็นก้อนต่อวัน × ช่องทาง
> ซึ่งเขียนเป็น SQL ชั้นเดียวไม่ได้ และแดชบอร์ดต้องอ่านเร็ว
> แต่ตารางนี้ **สร้างใหม่ทั้งหมดทุกรอบ** จากข้อมูลดิบ + บัญชีปรับ ไม่มีสถานะค้าง

### 2.3 `sales_report_day` — รายงานขาย Excel เป็น input ของ pipeline

แทน `DAY_TARGET` (181 วัน) และ `DAY_CH_TARGET` ที่เคยฝังอยู่ในโค้ด

```sql
CREATE TABLE sales_report_day(
  report_date  TEXT NOT NULL,           -- '2026-03-14'
  channel      TEXT NOT NULL,           -- Facebook | Sale Page | Shopee | Line OA | Tiktok
                                        -- | Lazada | Instagram | CRM | Other | '__TOTAL__'
  target_inc_vat REAL NOT NULL,
  source_file  TEXT,                    -- ชื่อไฟล์ Excel ที่มา
  source_sheet TEXT,                    -- ชื่อชีต (เดือน)
  loaded_at    TEXT,
  PRIMARY KEY (report_date, channel));
```

- แถว `channel = '__TOTAL__'` คือคอลัมน์ V ของรายงาน (ผลรวมทุกช่องทาง หักของส่งคืนแล้ว)
- ผลรวมของทุกช่องทางในวันหนึ่ง ๆ ต้องเท่ากับ `__TOTAL__` ของวันนั้น — ระบบจะตรวจให้ (§5)

### 2.4 `revenue_truth` — ขยายของเดิม

```sql
-- มีอยู่แล้ว: system, external_id, settled_gross, report_file, loaded_at
ALTER TABLE revenue_truth ADD COLUMN report_month TEXT;    -- '2026-03'
ALTER TABLE revenue_truth ADD COLUMN settled_at   TEXT;    -- วันที่แพลตฟอร์มจ่าย (ถ้ามี)
ALTER TABLE revenue_truth ADD COLUMN note         TEXT;
```

### 2.5 `revenue_rule_run` — สรุปว่ารอบนี้แต่ละกฎทำอะไรไปบ้าง

```sql
CREATE TABLE revenue_rule_run(
  run_id        INTEGER NOT NULL,
  rule          TEXT NOT NULL,
  n_orders      INTEGER,
  total_delta   REAL,
  date_from     TEXT,
  date_to       TEXT,
  detail        TEXT,                   -- JSON เช่น วันที่เกลี่ยไม่ได้ / ช่องทางที่ไม่มีออเดอร์
  PRIMARY KEY (run_id, rule));
```

### 2.6 `source_audit` — ยอดดิบใบนี้มาจากไฟล์ไหน แถวไหน

ตอบข้อ 7 ของโจทย์: "raw แต่ละ order มาจาก source/file ไหน และถ้ามี mapping ให้ตรวจย้อนกลับได้"

```sql
CREATE TABLE source_audit(
  order_id    TEXT NOT NULL,
  file_sha    TEXT NOT NULL,          -- sha256 ของไฟล์ -> ไฟล์เดิมโหลดซ้ำได้ผลเท่าเดิม
  loader      TEXT,                   -- gosell | oldsheet | pancake | mycloud
  file_name   TEXT,
  sheet       TEXT,
  source_row  INTEGER,                -- เลขแถวตามที่เห็นใน Excel (นับ 1)
  source_key  TEXT,                   -- เลขที่คำสั่งซื้อ / ชีต#แถว
  raw_inc_vat REAL,                   -- ยอดที่ loader เก็บลง orders (รวม VAT)
  raw_field   TEXT,                   -- อ่านจากคอลัมน์ไหน คิดยังไง
  mapping     TEXT,                   -- JSON: ช่องทางดิบ -> ที่ใช้ · SKU ที่เดา · รายการที่แตกจากโปรโมชั่น
  run_id      INTEGER,
  loaded_at   TEXT,
  PRIMARY KEY (order_id, file_sha));
```

หนึ่งแถวต่อ (ออเดอร์ × ไฟล์) — ออเดอร์เดียวโผล่หลายไฟล์ได้ และเห็นว่าไฟล์ไหนเขียนค่าอะไรไว้

```
python revenue_report.py OLD-20260303-01
```
พิมพ์ทั้งที่มา (ไฟล์/ชีต/แถว/คอลัมน์/mapping) และทุกชั้นที่ปรับยอดในหน้าเดียว

### 2.7 คอลัมน์ที่เพิ่มใน `orders`

```sql
ALTER TABLE orders ADD COLUMN gosell_channel TEXT;   -- ช่องทางดิบจากไฟล์ต้นทาง
                                                     -- ใช้ทำ xl_bucket (index.html: XL_GS_MAP)
ALTER TABLE orders ADD COLUMN data_state TEXT;       -- raw | pre_reconciled
ALTER TABLE orders ADD COLUMN page_key TEXT;         -- เพจ (ใช้กับ Conversion)
```

`orders.amount_inc_vat` / `amount_ex_vat` **จะไม่ถูกเขียนทับโดยชั้นกระทบยอดอีกต่อไป**

### 2.8 เพดานการเกลี่ย `calibration_max_ratio`

`config/revenue_rules.json → calibration_max_ratio` (ค่าเริ่มต้น `5.0` · ใส่ `0` เพื่อปิด)

แดชบอร์ดเดิมเกลี่ยตอนมีข้อมูลครบทั้งวัน อัตราจึงใกล้ 1 เสมอ แต่ pipeline โหลดทีละไฟล์
ถ้าวันไหนยอดดิบมาไม่ครบ ออเดอร์ไม่กี่ใบที่มีจะถูกดันให้พองหลายเท่าเพื่อให้รวมเท่าเป้า
เกินเพดานเมื่อไร ช่องทางนั้นของวันนั้นไม่ถูกเกลี่ย (ยอดที่แสดง = ยอดดิบ) และขึ้นเตือนในรายงาน

**วิธีใช้ที่ถูก: โหลดไฟล์ต้นทางให้ครบทั้งเดือนก่อน แล้วค่อยอ่านยอด**

---

## 3 · ตอบ 3 คำถามที่ต้องการ

### ยอดดิบจาก source เท่าไร

```sql
SELECT order_id, source, order_date, amount_inc_vat AS ยอดดิบ
FROM orders WHERE order_id = 'MKP-584862694109644066';
```

### ถูกปรับด้วย rule อะไร

```sql
SELECT layer, rule, amount_before, amount_after, delta, ratio,
       source_kind, source_ref, bucket, computed_at
FROM revenue_adjustment
WHERE order_id = 'MKP-584862694109644066'
ORDER BY layer;
```

ตัวอย่างผลลัพธ์ที่จะได้:

| layer | rule | before | after | delta | source_kind | source_ref |
|---:|---|---:|---:|---:|---|---|
| 1 | `mkp_settled` | 1,200.00 | 883.72 | −316.28 | `platform_report` | `shopee/584862694109644066` |

อีกตัวอย่าง ออเดอร์ที่ไม่ถูกล็อก:

| layer | rule | before | after | delta | source_kind | source_ref | bucket |
|---:|---|---:|---:|---:|---|---|---|
| 3 | `daily_calibration` | 990.00 | 1,012.35 | +22.35 | `sales_report_excel` | `2026-03-14\|Facebook` | Facebook |

### ยอดที่ใช้แสดงบน Dashboard เท่าไร

```sql
SELECT raw_inc_vat AS ดิบ, reported_inc_vat AS ที่แสดง,
       total_delta AS ส่วนต่าง, total_ratio, rules_applied, is_locked
FROM order_revenue WHERE order_id = 'MKP-584862694109644066';
```

### สรุปทั้งเดือน — ดิบ vs ที่แสดง vs รายงาน Excel

```sql
SELECT substr(o.order_date,1,7)               AS เดือน,
       ROUND(SUM(r.raw_inc_vat), 2)           AS ยอดดิบ,
       ROUND(SUM(r.reported_inc_vat), 2)      AS ยอดที่แสดง,
       ROUND(SUM(r.total_delta), 2)           AS ส่วนต่าง,
       (SELECT ROUND(SUM(target_inc_vat), 2) FROM sales_report_day s
         WHERE s.channel = '__TOTAL__'
           AND substr(s.report_date,1,7) = substr(o.order_date,1,7)) AS รายงาน_Excel
FROM orders o JOIN order_revenue r ON r.order_id = o.order_id
WHERE COALESCE(o.is_cancelled,0) = 0
GROUP BY เดือน ORDER BY เดือน;
```

---

## 4 · การรันซ้ำ และการแก้ด้วยมือ

| กฎ | รันซ้ำแล้วเป็นยังไง |
|---|---|
| `mkp_settled` · `mkp_factor` · `daily_calibration` | **คำนวณใหม่ทั้งหมด** ทุกรอบ — ลบแถวของกฎเหล่านี้ทิ้งแล้วสร้างใหม่ ผลจะเท่าเดิมเสมอถ้า input เท่าเดิม |
| `manual_cancel_amount` | คำนวณใหม่จากไฟล์ยกเลิกล่าสุด |
| `manual_override` | **ไม่ถูกลบ** — คนกรอกเองแล้วอยู่ถาวรจนกว่าจะลบเอง และมีลำดับสูงสุด |

คำสั่งที่จะเพิ่ม:

```
python daily_pipeline.py                    # กระทบยอดให้อัตโนมัติทุกรอบ
python daily_pipeline.py --no-reconcile     # ข้ามชั้นกระทบยอด ดูยอดดิบล้วน
python revenue_report.py 2026-03            # รายงานว่าดิบ vs ที่แสดง ต่างกันตรงไหน
```

`--no-reconcile` สำคัญ เพราะทำให้เทียบ "ยอดดิบ" กับ "ยอดที่แสดง" ได้โดยไม่ต้องแก้อะไร

---

## 5 · การตรวจที่จะเพิ่มใน `validate.py`

| กฎ | ระดับ | ตรวจอะไร |
|---|---|---|
| `sales_report_channel_sum` | error | ผลรวมทุกช่องทางของวันหนึ่ง ≠ `__TOTAL__` ของวันนั้น |
| `sales_report_gap` | warning | วันที่มีออเดอร์แต่ไม่มีเป้าในรายงาน Excel |
| `calibration_orphan_channel` | warning | รายงานมียอดช่องทางนั้น แต่วันนั้นไม่มีออเดอร์เลย (ยอดถูกเกลี่ยไปช่องทางอื่น) |
| `calibration_large_ratio` | warning | ออเดอร์ที่ถูกปรับเกิน ±20% — อาจแปลว่าจับช่องทางผิด |
| `revenue_truth_missing` | warning | ออเดอร์ marketplace ในช่วงที่ควรมียอดจริง แต่ไม่มีใน `revenue_truth` |
| `reported_vs_report` | warning | ยอดที่แสดงรายเดือน ≠ รายงาน Excel เกิน 1 บาท |

---

## 6 · ไฟล์ที่จะเพิ่ม / แก้

| ไฟล์ | ทำอะไร |
|---|---|
| `config/revenue_rules.json` | **ใหม่** — `mkp_fix_until`, `mkp_channels`, `mkp_factor`, `xl_buckets`, `xl_gs_map`, สวิตช์เปิด/ปิดแต่ละ layer |
| `pipeline/revenue.py` | **ใหม่** — คำนวณ 4 layer แล้วเขียน `revenue_adjustment` + `order_revenue` |
| `pipeline/loaders/sales_report.py` | **ใหม่** — อ่านไฟล์ "รายงานขายฮีลมี 2569" เข้า `sales_report_day` |
| `migrate_day_target.py` | **ใหม่** — ดึง `DAY_TARGET` + `DAY_CH_TARGET` จาก `index.html` เข้า `sales_report_day` (รันครั้งเดียว) |
| `pipeline/db.py` | เพิ่มตาราง + คอลัมน์ข้างบน |
| `pipeline/reconcile.py` | ย้ายการเขียนทับยอดออกไปเป็น adjustment แทน |
| `pipeline/exports/dashboard.py` | อ่านจาก `order_revenue` แทน `orders` · ปรับยอดรายสินค้าด้วย `total_ratio` · เพิ่ม `revenue_audit.json` |
| `pipeline/validate.py` | เพิ่มกฎ §5 |
| `daily_pipeline.py` | เพิ่มขั้นตอน "กระทบยอดรายได้" + `--no-reconcile` |
| `revenue_report.py` | **ใหม่** — รายงานเทียบดิบ vs ที่แสดง รายเดือน × ช่องทาง |

`config/rules.json → manual_cancel_overrides_amount` จะถูก **ลบทิ้ง** (ย้ายไปเป็น layer 4 แทน)

---

## 7 · ผลการตรวจไฟล์รายงานขายจริง (10 ก.ย. 2026)

ได้ไฟล์ "รายงานขายฮีลมี 2569" มาแล้ว — 6 ชีต (มกราคม–มิถุนายน) 181 วัน

### 7.1 โครงไฟล์

    A  วันที่สั่งซื้อ        I  ส่งคืนสำเร็จ      L-T ช่องทาง 9 ช่อง
    B  คำสั่งซื้อทั้งหมด      J  ยอดขายรวม        U   Upsell
    C-H รอ/กำลัง/สำเร็จ      K  Facebook (คีย์)   V   ยอดรวม
                              (ไม่ใช้ ใช้ L แทน)

### 7.2 ★ เจอจุดที่คอมเมนต์ในโค้ดเดิมเขียนไม่ตรงกับไฟล์จริง

คอมเมนต์ใน `index.html` บรรทัด 1363 บอกว่า `V = L+M+N+O+P+Q+R+S+T+U−I`
ลองคำนวณตามสูตรนั้นจริง **ไม่ตรง 15 วันจาก 181 วัน** เพราะสูตรในชีตไม่เหมือนกันทุกแถว —
15 วันนั้นไม่ได้บวก Upsell เข้าไป (24 · 26 · 27 · 28 · 31 มี.ค. · 1 · 2 · 10 · 13 · 15 · 16 · 17 · 20 · 23 · 24 เม.ย.)

**การอ่านค่าที่เก็บไว้ในเซลล์ V ตรง ๆ ตรงกับ `DAY_TARGET` เดิม 181 / 181 วัน**
loader จึงอ่านคอลัมน์ V ไม่คำนวณเอง และมีการนับไว้ว่ากี่วันที่สูตรไม่ตรง เพื่อให้เห็นในรายงาน

### 7.3 สูตรเป้ารายช่องทาง — พิสูจน์แล้ว

    เป้าของช่องทาง = ค่าในคอลัมน์นั้น x (V / ผลรวม L:T)

คือเกลี่ย Upsell และการหักของส่งคืนเข้าไปตามสัดส่วน โดย **Upsell ไม่อยู่ในตัวหาร**
ตรวจกับ `DAY_CH_TARGET` เดิม **946 / 946 ช่อง** คลาดเคลื่อนสูงสุด 0.05 บาท (การปัดเศษ)

### 7.4 ★★ เรื่องใหญ่ที่สุด — ข้อมูลในฐานตอนนี้ผ่านการเกลี่ยมาแล้ว

เทียบยอดรายวันในตาราง `orders` ช่วง ม.ค.–มิ.ย. กับ `DAY_TARGET`:

| ผลการเทียบ | จำนวนวัน |
|---|---:|
| ตรงเป๊ะ (ต่างไม่เกิน 0.05 บาท) | **148 / 181** |
| ต่างไม่เกิน 0.08 บาท (ปัดเศษ) | 33 |
| ต่างเกินกว่านั้น | **0** |

ทั้งช่วง 181 วัน ยอดรวมต่างกัน **0.12 บาท**

แถวใน `orders` ตอนนี้มาจาก `hylme_transaction_*.xlsx` ซึ่งเป็นไฟล์ที่ **แดชบอร์ด export ออกมาหลังเกลี่ยแล้ว**
พูดอีกอย่างคือ ยอดที่เก็บอยู่ในฐานตอนนี้คือ **reported revenue ไม่ใช่ raw**

**สิ่งที่ทำ** — เพิ่มคอลัมน์ `orders.data_state`

| ค่า | หมายถึง | ชั้น 1-3 ทำอะไร |
|---|---|---|
| `raw` | ยอดดิบจากไฟล์ต้นทางผ่าน pipeline | ปรับตามปกติ |
| `pre_reconciled` | ยอดที่ผ่านการเกลี่ยมาแล้ว (18,326 แถวเดิม) | **ข้ามทั้งหมด** ไม่เกลี่ยซ้ำ |

จะได้ raw จริงของ ม.ค.–มิ.ย. ต่อเมื่อพอร์ต loader ของ GoSell และ Google Sheet เก่า (`LOGIC_MAP.md` §8.8)
แล้วโหลดไฟล์ต้นฉบับเข้ามาใหม่ — ตอนนั้นชั้นเกลี่ยจะเริ่มทำงานกับข้อมูลจริง

### 7.5 ก.ค. เป็นต้นไป — ยืนยันแล้วว่าใช้ข้อมูลจริง ไม่เกลี่ย

ตั้ง `calibration_until = "2026-06"` ใน `config/revenue_rules.json`
และเกลี่ยเฉพาะวันที่มีเป้าในตาราง — ก.ค.–ส.ค. จึงใช้ยอดดิบล้วนตามที่ตกลง

### 7.6 เจอโค้ดตายใน index.html

บรรทัด 6556 ประกาศ `const locked = new Set()` เพื่อกันไม่ให้ออเดอร์ marketplace
ที่เพิ่งใส่ยอดจริงไปโดนเกลี่ยซ้ำในชั้นที่ 3 — แต่ **ตัวแปรนี้ไม่เคยถูกอ่านเลย**
พฤติกรรมจริงคือ marketplace โดนเกลี่ยด้วย

ตั้งค่าเริ่มต้น `respect_mkp_lock = false` = ทำเหมือนของเดิมเป๊ะ ตัวเลขตรงกัน
ถ้าอยากให้ยอดที่แพลตฟอร์มจ่ายจริงไม่ถูกแตะ เปลี่ยนเป็น `true` ได้ แต่ตัวเลขจะต่างจากเดิม

### 7.7 ทดสอบเครื่องเกลี่ยด้วยข้อมูลดิบสังเคราะห์

ใส่ออเดอร์ดิบ 5 ใบ รวม 30,000 บาท ในวันที่เป้า = 38,492.50

| ออเดอร์ | ยอดดิบ | ยอดที่แสดง | อัตรา | ช่องทาง |
|---|---:|---:|---:|---|
| RAWTEST-1 | 12,000.00 | 14,803.79 | 1.2336 | Facebook → Facebook |
| RAWTEST-2 | 8,000.00 | 9,869.19 | 1.2336 | Facebook → Facebook |
| RAWTEST-3 | 5,000.00 | 5,148.06 | 1.0296 | Shopee → Shopee |
| RAWTEST-4 | 3,000.00 | 6,204.16 | 2.0681 | Line OA → **CRM** |
| RAWTEST-5 | 2,000.00 | 2,467.30 | 1.2336 | (ระบุไม่ได้) → **Facebook** |
| **รวม** | **30,000.00** | **38,492.50** | | ตรงเป้าพอดี ต่าง 0.0000 |

- RAWTEST-4 ช่องทาง Line OA แต่วันนั้นรายงานไม่มียอด Line OA → ถูกโยนกลับเข้ากองรอจัด แล้วลง CRM
- RAWTEST-5 อ่านช่องทางไม่ออก → ลงช่องทางที่ยอดยังขาดมากที่สุด
- **ยอดดิบใน `orders` ไม่ถูกแตะเลยแม้แต่ใบเดียว** ตรวจแล้ว

## 8 · ผลที่จะได้หลังทำเสร็จ

| คำถาม | ตอบจากตารางไหน |
|---|---|
| ยอดดิบเดือน มี.ค. เท่าไร | `orders` |
| ทำไมยอด Shopee ถึงลดลง 316 บาทในออเดอร์นี้ | `revenue_adjustment` แถว `mkp_settled` พร้อม `source_ref` |
| แดชบอร์ดแสดงเท่าไร | `order_revenue.reported_inc_vat` |
| ตรงกับรายงาน Excel ไหม | `sales_report_day` เทียบกับ `order_revenue` |
| รอบนี้ปรับไปกี่ใบ กี่บาท | `revenue_rule_run` |
| ถ้าไม่เกลี่ยเลยจะเป็นยังไง | `daily_pipeline.py --no-reconcile` |

ไม่มีขั้นตอนไหนเขียนทับยอดดิบ — ย้อนกลับไปดูของเดิมได้ตลอด และเลิกใช้ชั้นกระทบยอดเมื่อไหร่ก็ได้
โดยไม่ต้องโหลดข้อมูลใหม่
