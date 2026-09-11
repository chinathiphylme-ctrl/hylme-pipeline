# -*- coding: utf-8 -*-
"""อ่านค่าจาก config/*.json

ทุกค่าที่เคยฝังอยู่ใน index.html ย้ายมาอยู่ในไฟล์ JSON แล้ว
แก้ไฟล์ JSON แล้วรันใหม่ ไม่ต้องแตะโค้ด
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
ARCHIVE_DIR = DATA_DIR / "archive"
OUTPUT_DIR = DATA_DIR / "output"


def _load(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"ไม่พบไฟล์ตั้งค่า {path}\n"
            f"ตรวจว่ารันคำสั่งจากในโฟลเดอร์ hylme_pipeline และโฟลเดอร์ config/ ยังอยู่ครบ"
        )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _strip_comments(d: dict) -> dict:
    """ตัดคีย์ที่ขึ้นต้นด้วย _ ออก (เป็นคอมเมนต์อธิบาย ไม่ใช่ค่าจริง)"""
    return {k: v for k, v in d.items() if not k.startswith("_")}


class Config:
    """ค่าตั้งทั้งหมดของ pipeline — สร้างครั้งเดียวแล้วส่งต่อไปทุกโมดูล"""

    def __init__(self):
        self.rules = _strip_comments(_load("rules.json"))
        self.products = _strip_comments(_load("products.json"))
        self.mappings = _strip_comments(_load("mappings.json"))
        self.channels = _strip_comments(_load("channels.json"))
        self.statuses = _strip_comments(_load("statuses.json"))
        self.revenue = _strip_comments(_load("revenue_rules.json"))
        self.validation = _strip_comments(_load("validation.json"))
        self.live_sources = set(self.validation.get("live_sources") or [])

        # ---- ค่าที่ใช้บ่อย ----
        self.vat_rate: float = self.rules["vat_rate"]
        self.biz_day_start_hour: int = self.rules["biz_day_start_hour"]

        # กติกาตัดรหัสประเทศตอนแกะเบอร์จากข้อความอิสระ (คำอธิบายอยู่ที่ util.extract_thai_phone)
        # ค่าเริ่มต้น false = ผลเหมือน index.html เดิมทุกประการ
        from . import util as _util
        _util.set_phone_country_code_mode(self.rules.get("phone_country_code_strict", False))

        # ---- regex ที่คอมไพล์ไว้ล่วงหน้า ----
        self.re_mc_cancel = re.compile(self.statuses["mc_cancel_re"], re.I)
        self.re_pancake_cancel = re.compile(self.statuses["pancake_cancel_re"], re.I)
        self.re_returned = re.compile(self.statuses["returned_re"], re.I)
        self.re_marketplace = re.compile(self.channels["marketplace_re"], re.I)
        self.re_mkp_channel = re.compile(self.channels["mkp_channel_re"], re.I)
        self.re_cancel_manual_sheet = re.compile(self.statuses["cancel_manual_sheet_re"], re.I)
        self.re_s9 = re.compile(self.products["re_s9"], re.I)
        self.re_v9 = re.compile(self.products["re_v9"], re.I)
        self.product_aliases = [(re.compile(p, re.I), name) for p, name in self.products["product_aliases"]]
        self.mc_channel_alias = [(re.compile(p, re.I), name) for p, name in self.channels["mc_channel_alias"]]

        self.mc_ok_status = set(self.statuses["mc_ok_status"])
        self.preorder_skus = {s.upper() for s in self.products["preorder_skus"]}
        self.olive_skus = {s.upper() for s in self.products["olive_skus"]}
        self.shipping_labels = [s.lower() for s in self.products["shipping_labels"]]
        self.non_product_labels = [s.lower() for s in
                                   self.products.get("non_product_labels", [])]
        self.metro_provinces = set(self.channels["metro_provinces"])

        # ---- ชื่อคน: เก็บทั้งแบบตรงตัวและแบบไม่สนตัวพิมพ์ (index.html: SALES_PERSON_MAP_CI) ----
        self.sales_person_map = self.mappings["sales_person_map"]
        self.sales_person_map_ci = {k.lower(): v for k, v in self.sales_person_map.items()}
        self.telesales_people = set(self.mappings["telesales_people"])
        self.owner_default_channel = self.mappings.get("owner_default_channel", {})
        self.pancake_rep_map = {k.lower(): v for k, v in self.mappings["pancake_rep_map"].items()}

        # ---- ทีม ----
        self.teams = self.channels["teams"]
        self.team_of_person = {p: t["key"] for t in self.teams for p in t["people"]}
        self.team_of_channel = {c: t["key"] for t in self.teams for c in t["channels"]}

        self.ch = self.channels["mc_sales_channel_codes"]

    # ------------------------------------------------------------------ paths
    @property
    def db_path(self) -> Path:
        """path ของ master database

        ตั้ง environment variable HYLME_DB เพื่อชี้ไปฐานข้อมูลอื่นได้ชั่วคราว
        (ใช้ตอนทดลองกับสำเนา โดยไม่ต้องแก้ config/rules.json)
        """
        env = os.environ.get("HYLME_DB")
        p = Path(env) if env else Path(self.rules["db_path"])
        return p if p.is_absolute() else (ROOT / p).resolve()

    def ensure_dirs(self) -> None:
        for d in (RAW_DIR, ARCHIVE_DIR, OUTPUT_DIR):
            d.mkdir(parents=True, exist_ok=True)


_INSTANCE: Config | None = None


def get_config() -> Config:
    """คืน Config ตัวเดียวกันทุกครั้ง (อ่านไฟล์แค่รอบแรก)"""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = Config()
    return _INSTANCE
