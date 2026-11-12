"""Alerte qualité vers un salon Discord (webhook), lu par l'équipe.

Sans webhook configuré, l'alerte est seulement journalisée (niveau ERROR) : le pipeline ne
tombe jamais à cause du canal d'alerte.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

DISCORD_MAX = 2000  # longueur maximale d'un message Discord


def format_alert(day: str, failed: list[dict], report: dict | None = None) -> str:
    lines = [f"🚨 **La gazette — contrôle qualité en échec** ({day})"]
    for c in failed:
        line = f"• `{c['controle']}` : valeur {c['valeur']} (seuil {c['seuil']})"
        if c.get("detail"):
            line += f" — {c['detail']}"
        lines.append(line)
    if report:
        lines.append(
            f"Passage : {report['entrees']} entrés, {report['gardes']} gardés, "
            f"{report['rejetes']} rejetés. Rapport : data/quality/{day}/rapport.md"
        )
    message = "\n".join(lines)
    return message if len(message) <= DISCORD_MAX else message[: DISCORD_MAX - 1] + "…"


def send_alert(webhook_url: str, message: str, *, client: httpx.Client | None = None) -> bool:
    """Envoie l'alerte. Renvoie True si Discord l'a acceptée. Ne lève jamais."""
    log.error("ALERTE QUALITÉ\n%s", message)
    if not webhook_url:
        log.warning("GAZETTE_ALERT_WEBHOOK vide : alerte seulement journalisée.")
        return False
    owned = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        response = client.post(webhook_url, json={"content": message, "username": "La gazette"})
        if response.status_code >= 400:
            log.error("Webhook refusé : HTTP %s %s", response.status_code, response.text[:200])
            return False
        return True
    except httpx.HTTPError as exc:
        log.error("Webhook injoignable : %s", exc)
        return False
    finally:
        if owned:
            client.close()
