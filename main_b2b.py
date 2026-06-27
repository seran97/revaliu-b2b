# -*- coding: utf-8 -*-
"""
main_b2b.py — Orquestador del Radar Corporativo B2B (Revaliu)
Corre autónomamente: blog 2-3x semana + Telegram heartbeat
"""
from __future__ import annotations
import os, sys, time, threading
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "radar_nichos" / ".env")

import requests

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SITE_URL         = "https://revaliu.com"

# ── Telegram ───────────────────────────────────────────────────────────────────

def tg(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


# ── Schedule ───────────────────────────────────────────────────────────────────

def _days_since_last_run(marker: Path) -> float:
    if not marker.exists():
        return 999
    return (datetime.now() - datetime.fromtimestamp(marker.stat().st_mtime)).total_seconds() / 86400


def should_run_blog() -> bool:
    marker = Path(__file__).parent / ".last_blog_run"
    return _days_since_last_run(marker) >= 3.0  # cada 3 días ~2x semana


def mark_blog_ran():
    (Path(__file__).parent / ".last_blog_run").touch()


# ── Main Loop ──────────────────────────────────────────────────────────────────

def main():
    tg(
        f"🚀 <b>Revaliu B2B — Sistema activo</b>\n"
        f"Blog automático: 2-3 artículos/semana\n"
        f"Sitio: {SITE_URL}\n"
        f"📲 WhatsApp: +57 318 432 2874"
    )

    print("╔══════════════════════════════════════╗")
    print("║   Revaliu B2B — Motor SEO Activo     ║")
    print("╚══════════════════════════════════════╝")

    ciclo = 0
    while True:
        ciclo += 1
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Ciclo #{ciclo}")

        # Blog: cada 3 días
        if should_run_blog():
            print("  Corriendo blog pipeline...")
            try:
                from blog_pipeline import run as run_blog
                n = run_blog(max_articles=2)
                mark_blog_ran()
                if n > 0:
                    tg(
                        f"📝 <b>Revaliu Blog — {n} artículo(s) nuevos</b>\n"
                        f"Publicados en {SITE_URL}/blog/\n"
                        f"Motor: RSS → Gemini AI → HTML automático"
                    )
            except Exception as e:
                print(f"  [Blog] Error: {e}")
                tg(f"⚠️ Blog pipeline error: {str(e)[:150]}")
        else:
            print("  Blog: próximo run en < 3 días — saltando")

        # Dormir 24h
        proxima = (datetime.now() + timedelta(hours=24)).strftime("%d/%m %H:%M")
        print(f"  Durmiendo 24h · próximo: {proxima}")
        tg(f"💓 Revaliu B2B vivo · Ciclo #{ciclo} · Próximo blog check: {proxima}")
        time.sleep(86400)


if __name__ == "__main__":
    main()
