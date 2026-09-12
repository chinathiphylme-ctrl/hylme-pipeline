# -*- coding: utf-8 -*-
"""เปิดแดชบอร์ด — ยิงเว็บเซิร์ฟเวอร์เล็ก ๆ ในเครื่องแล้วเปิดเบราว์เซอร์ให้

    python serve_dashboard.py

ทำไมต้องมีสคริปต์นี้: `dashboard.html` อ่านตัวเลขจากไฟล์ .json ข้าง ๆ
ถ้าดับเบิลคลิกเปิดไฟล์ตรง ๆ (file://) เบราว์เซอร์จะบล็อกการอ่านไฟล์ข้างเคียง
เป็นกติกาความปลอดภัยของเบราว์เซอร์เอง แก้ไม่ได้จากฝั่งหน้าเว็บ
เสิร์ฟผ่าน http://127.0.0.1 แล้วทุกอย่างทำงานปกติ

ตัวเลือก
    python serve_dashboard.py --port 9000     เปลี่ยนพอร์ต
    python serve_dashboard.py --no-browser    ไม่ต้องเปิดเบราว์เซอร์ให้

เซิร์ฟเวอร์นี้ผูกกับ 127.0.0.1 เท่านั้น เครื่องอื่นในวงแลนเข้าไม่ได้
กด Ctrl+C เพื่อปิด
"""
from __future__ import annotations

import argparse
import http.server
import socketserver
import sys
import threading
import webbrowser
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASH_JSON = ROOT / "data" / "output" / "dashboard"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """เงียบกว่าเดิม — ไม่พ่น log ทุก request ให้รก"""

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="เปิดแดชบอร์ดในเบราว์เซอร์")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    if not (ROOT / "dashboard.html").exists():
        print("ไม่พบ dashboard.html — ต้องรันจากในโฟลเดอร์ hylme_pipeline")
        return 1
    if not DASH_JSON.exists() or not any(DASH_JSON.glob("*.json")):
        print("ยังไม่มีไฟล์สรุปใน data/output/dashboard/")
        print("รัน  python daily_pipeline.py  ก่อนหนึ่งรอบ แล้วค่อยเปิดแดชบอร์ด")
        return 1

    handler = partial(QuietHandler, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    port = args.port
    for _ in range(20):
        try:
            httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            port += 1
    else:
        print(f"หาพอร์ตว่างไม่ได้ (ลองตั้งแต่ {args.port})")
        return 1

    url = f"http://127.0.0.1:{port}/dashboard.html"
    n = len(list(DASH_JSON.glob("*.json")))
    size = sum(p.stat().st_size for p in DASH_JSON.glob("*.json"))
    print()
    print(f"  แดชบอร์ด: {url}")
    print(f"  ไฟล์สรุป {n} ไฟล์ รวม {size/1024:,.0f} KB")
    print("  กด Ctrl+C เพื่อปิดเซิร์ฟเวอร์")
    print()
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nปิดเซิร์ฟเวอร์แล้ว")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
