#!/usr/bin/env python3
"""
Surveillance de stock Shopify -> alerte e-mail.

Compare l'etat actuel des variantes (tailles) avec l'etat sauvegarde
dans state.json. Envoie un mail uniquement sur les transitions
"indisponible -> disponible" ou "nouveau produit deja dispo".

Aucune dependance externe : uniquement la bibliotheque standard Python.
"""

import json
import os
import smtplib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

# --- Configuration (via variables d'environnement) ---------------------------

SHOP = os.environ.get("SHOP_URL", "https://skylrk.com").rstrip("/")
KEYWORDS = [k.strip().lower() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
MAIL_TO = os.environ.get("MAIL_TO") or SMTP_USER

FORCE_TEST = os.environ.get("FORCE_TEST", "").lower() in ("1", "true", "yes")

UA = "Mozilla/5.0 (compatible; personal-restock-monitor/1.0)"


# --- Recuperation du catalogue ----------------------------------------------

def http_json(url, timeout=30):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_products():
    """Recupere tous les produits via /products.json (pagine par 250)."""
    products, page = [], 1
    while page <= 10:  # garde-fou
        batch = http_json(f"{SHOP}/products.json?limit=250&page={page}").get("products", [])
        if not batch:
            break
        products.extend(batch)
        page += 1
    return products


def fetch_with_retry(attempts=3, delay=15):
    last = None
    for i in range(attempts):
        try:
            return fetch_products()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            last = exc
            print(f"[warn] tentative {i + 1}/{attempts} echouee : {exc}", file=sys.stderr)
            if i < attempts - 1:
                time.sleep(delay)
    raise SystemExit(f"[error] impossible de recuperer le catalogue : {last}")


# --- Construction de l'etat --------------------------------------------------

def matches(product):
    if not KEYWORDS:
        return True
    haystack = f"{product.get('title', '')} {product.get('handle', '')}".lower()
    return any(k in haystack for k in KEYWORDS)


def build_snapshot(products):
    """{variant_id: {infos...}} pour les produits qui matchent les mots-cles."""
    snap = {}
    for p in products:
        if not matches(p):
            continue
        for v in p.get("variants", []):
            snap[str(v["id"])] = {
                "product": p.get("title", ""),
                "handle": p.get("handle", ""),
                "size": v.get("title", ""),
                "price": v.get("price", ""),
                "available": bool(v.get("available")),
            }
    return snap


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(snapshot):
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")


# --- E-mail ------------------------------------------------------------------

def send_mail(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = MAIL_TO
    msg.set_content(body)

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
    print(f"[ok] mail envoye a {MAIL_TO}")


def format_alert(restocked):
    lines = ["RETOUR EN STOCK\n"]
    by_product = {}
    for info in restocked:
        by_product.setdefault((info["product"], info["handle"]), []).append(info)

    for (title, handle), items in by_product.items():
        sizes = ", ".join(sorted(i["size"] for i in items))
        price = items[0]["price"]
        lines.append(f"{title}")
        lines.append(f"  Tailles : {sizes}")
        lines.append(f"  Prix    : {price}")
        lines.append(f"  Lien    : {SHOP}/products/{handle}")
        lines.append("")

    lines.append(f"Detecte le {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


# --- Programme principal -----------------------------------------------------

def main():
    products = fetch_with_retry()
    current = build_snapshot(products)
    print(f"[info] {len(products)} produits recuperes, {len(current)} variantes surveillees")

    if not current:
        print("[warn] aucune variante ne correspond aux mots-cles : " + ", ".join(KEYWORDS))

    if FORCE_TEST:
        dispo = [v for v in current.values() if v["available"]]
        send_mail(
            "[TEST] Surveillance de stock operationnelle",
            f"Configuration OK.\n\n"
            f"Variantes surveillees : {len(current)}\n"
            f"Actuellement en stock : {len(dispo)}\n\n"
            + "\n".join(f"- {v['product']} / {v['size']} ({'DISPO' if v['available'] else 'rupture'})"
                        for v in current.values()),
        )

    previous = load_state()

    if previous is None:
        save_state(current)
        print("[info] premier passage : etat de reference enregistre, aucune alerte envoyee.")
        return

    restocked = [
        info for vid, info in current.items()
        if info["available"] and not previous.get(vid, {}).get("available", True)
    ]

    if restocked:
        subject = "STOCK : " + ", ".join(sorted({i["product"] for i in restocked}))
        send_mail(subject, format_alert(restocked))
    else:
        print("[info] aucun retour en stock detecte.")

    save_state(current)


if __name__ == "__main__":
    main()
