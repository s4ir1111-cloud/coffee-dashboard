#!/usr/bin/env python3
"""Send the generated monthly summary to Telegram without exposing secrets."""

import os
import sys
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MESSAGE_PATH = os.path.join(BASE_DIR, "telegram_monthly_report.txt")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required", file=sys.stderr)
        return 2
    with open(MESSAGE_PATH, encoding="utf-8") as source:
        text = source.read().strip()
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    if not response.ok:
        print(f"Telegram API HTTP {response.status_code}: {response.text[:300]}", file=sys.stderr)
        return 1
    print("Telegram monthly report sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
