# -*- coding: utf-8 -*-
"""รายงานผลการรันให้คนอ่าน

แสดงเป็น 'สิ่งที่เกิดขึ้นกับธุรกิจ' ไม่ใช่ศัพท์เทคนิค ตามหัวข้อ 19 ของสเปก
เขียนออกทั้งหน้าจอและไฟล์ data/output/run_report_<วันที่>.txt
"""
from __future__ import annotations

import sys
from pathlib import Path

OK = "[ OK ]"
WARN = "[ ! ]"
ERR = "[XXXX]"
INFO = "[ .. ]"

LINE = "─" * 62


def use_utf8_console() -> None:
    """ทำให้ terminal บน Windows แสดงภาษาไทยได้

    เรียกก่อน print อะไรก็ตาม ไม่งั้นจะเจอ UnicodeEncodeError บน cmd.exe รุ่นเก่า
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


class Report:
    def __init__(self, title: str):
        self.lines: list[str] = []
        self.title = title
        self.has_error = False
        self.has_warning = False

    # ------------------------------------------------------------------ เขียน
    def _add(self, text: str = "") -> None:
        self.lines.append(text)
        print(text, flush=True)

    def header(self) -> None:
        self._add()
        self._add(LINE)
        self._add(f"  {self.title}")
        self._add(LINE)

    def section(self, name: str) -> None:
        self._add()
        self._add(name.upper())

    def ok(self, label: str, detail: str = "") -> None:
        self._add(f"  {OK}  {label}" + (f"   {detail}" if detail else ""))

    def warn(self, label: str, detail: str = "") -> None:
        self.has_warning = True
        self._add(f"  {WARN}  {label}" + (f"   {detail}" if detail else ""))

    def error(self, label: str, detail: str = "") -> None:
        self.has_error = True
        self._add(f"  {ERR}  {label}" + (f"   {detail}" if detail else ""))

    def info(self, label: str, detail: str = "") -> None:
        self._add(f"  {INFO}  {label}" + (f"   {detail}" if detail else ""))

    def plain(self, text: str = "") -> None:
        self._add(text)

    def bullet(self, text: str) -> None:
        self._add(f"        - {text}")

    # ------------------------------------------------------------------ สรุปท้าย
    def footer(self, elapsed: float) -> None:
        self._add()
        self._add(LINE)
        if self.has_error:
            verdict = "มีข้อผิดพลาดที่ต้องแก้ก่อนใช้ไฟล์ output"
        elif self.has_warning:
            verdict = "รันสำเร็จ แต่มีรายการที่ควรตรวจสอบ"
        else:
            verdict = "รันสำเร็จ ไม่มีรายการค้าง"
        self._add(f"  {verdict}   (ใช้เวลา {elapsed:.1f} วินาที)")
        self._add(LINE)
        self._add()

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return path


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "-"


def fmt_baht(n) -> str:
    try:
        return f"{float(n):,.2f} บาท"
    except (TypeError, ValueError):
        return "-"
