"""Gmail SMTP(アプリパスワード)でメール送信するヘルパー。

プロジェクトルート(kabu/)の .env に以下を設定しておくこと:
  GMAIL_ADDRESS=your_address@gmail.com
  GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx (16桁、スペースなし)
  MAIL_TO=通知を受け取るアドレス(省略時はGMAIL_ADDRESS宛)
"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[4]
load_dotenv(ROOT / ".env")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_email(subject: str, body: str) -> None:
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    to_addr = os.environ.get("MAIL_TO") or address

    if not address or not app_password:
        raise RuntimeError(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD が .env に設定されていません。"
            ".claude/skills/stock-screening/.env.example を参照してください。"
        )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = address
    msg["To"] = to_addr

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(address, app_password.replace(" ", ""))
        server.sendmail(address, [to_addr], msg.as_bytes())


if __name__ == "__main__":
    import sys

    send_email(sys.argv[1] if len(sys.argv) > 1 else "テスト", sys.argv[2] if len(sys.argv) > 2 else "テストメールです。")
    print("送信しました")
