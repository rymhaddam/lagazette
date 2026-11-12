"""Enchaînements utilisés par la CLI et par le DAG : nettoyer un jour, puis le contrôler."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import httpx

from gazette.clean.models import load_rules_config
from gazette.clean.pipeline import clean_items, load_raw_day
from gazette.config import Settings
from gazette.quality.alert import format_alert, send_alert
from gazette.quality.checks import run_checks
from gazette.quality.report import build_report, read_report, write_report

log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def run_clean(
    settings: Settings, day: date, *, use_db: bool = True, now: datetime | None = None
) -> tuple[dict, list[dict]]:
    """Nettoie le brut du jour, alimente articles_propres / articles_rejetes et écrit le
    rapport (sans les contrôles). Renvoie (rapport, lignes gardées)."""
    config = load_rules_config(settings.rules_file)
    sources = load_raw_day(settings.raw_dir, day)
    if not sources:
        log.warning("Aucun fichier brut pour le %s dans %s", day, settings.raw_dir)
    result = clean_items(sources, day, config, now or utc_now())
    report = build_report(result, config)

    if use_db:
        from gazette.storage import db

        with db.connect(settings.database_url) as conn:
            report["stockage"] = db.replace_day(
                conn, day, result.gardes, result.rejets, result.passage_le
            )
            db.save_report(conn, day, result.passage_le, report)

    folder = write_report(settings.quality_dir, report)
    log.info("Rapport de qualité écrit dans %s", folder)
    return report, [a.to_row() for a in result.gardes]


def run_quality_checks(
    settings: Settings,
    day: date,
    *,
    use_db: bool = True,
    rows: list[dict] | None = None,
    overrides: dict[str, float] | None = None,
    alert_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Vérifie les faits sur les données du jour, publie les métriques, alerte si besoin.

    `rows` : articles à contrôler quand on travaille sans base ; sinon lus dans
    articles_propres. `overrides` : seuils de [controles] remplacés pour ce passage.
    """
    report = read_report(settings.quality_dir, day.isoformat())
    if report is None:
        raise FileNotFoundError(f"Pas de rapport pour le {day} : lancer le nettoyage d'abord.")
    config = load_rules_config(settings.rules_file)
    thresholds = {**config.checks, **(overrides or {})}
    passage_le = datetime.fromisoformat(report["passage_le"])

    if use_db:
        from gazette.storage import db

        with db.connect(settings.database_url) as conn:
            rows = db.fetch_day(conn, day)
            results = [
                c.to_dict()
                for c in run_checks(rows, report, thresholds, config.extrait_max, passage_le)
            ]
            db.save_metrics(conn, day, passage_le, results)
            report["controles"] = results
            db.save_report(conn, day, passage_le, report)
    else:
        results = [
            c.to_dict()
            for c in run_checks(rows or [], report, thresholds, config.extrait_max, passage_le)
        ]
        report["controles"] = results
    write_report(settings.quality_dir, report)

    failed = [c for c in results if not c["ok"]]
    for c in results:
        log.info(
            "%s %s : valeur=%s seuil=%s %s",
            "OK" if c["ok"] else "KO",
            c["controle"],
            c["valeur"],
            c["seuil"],
            c["detail"],
        )
    sent = False
    if failed:
        message = format_alert(day.isoformat(), failed, report)
        sent = send_alert(settings.alert_webhook, message, client=alert_client)
    return {
        "jour_collecte": day.isoformat(),
        "ok": not failed,
        "echecs": [c["controle"] for c in failed],
        "alerte_envoyee": sent,
        "metriques": {c["controle"]: c["valeur"] for c in results},
    }
