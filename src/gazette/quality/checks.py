"""Contrôles de données : des FAITS vérifiés sur les vraies données, pas des tests de code.

Ils tournent après chaque passage (tâche Airflow `quality_checks`) sur les lignes de
`articles_propres` du jour. Chaque contrôle renvoie une valeur mesurée, un seuil et ok/ko ;
les valeurs sont publiées en métriques, et tout contrôle faux déclenche une alerte.

Pourquoi des assertions maison plutôt que Great Expectations ou Soda : huit faits simples,
lisibles en une ligne chacun ; pas de dépendance lourde à faire cohabiter avec les
contraintes d'Airflow dans l'image ; et les résultats vont directement dans notre table
`metriques_qualite`, au format qu'on choisit pour le TD 8.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from gazette.clean.normalize import has_mojibake


@dataclass(frozen=True)
class CheckResult:
    controle: str
    ok: bool
    valeur: float
    seuil: float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _future(rows: list[dict], now: datetime, tolerance_min: int) -> CheckResult:
    limit = now + timedelta(minutes=tolerance_min)
    bad = [r for r in rows if r["publie_le"] > limit]
    return CheckResult(
        "C01_aucune_date_future",
        not bad,
        len(bad),
        0,
        ", ".join(f"{r['source_slug']}:{r['publie_le'].isoformat()}" for r in bad[:3]),
    )


def _empty_titles(rows: list[dict]) -> CheckResult:
    bad = [r for r in rows if not (r["titre"] or "").strip()]
    return CheckResult(
        "C02_aucun_titre_vide", not bad, len(bad), 0, ", ".join(r["id"][:10] for r in bad[:3])
    )


def _duplicate_links(rows: list[dict]) -> CheckResult:
    links = [r["lien"] for r in rows]
    dups = len(links) - len(set(links))
    return CheckResult("C03_aucun_doublon_lien", dups == 0, dups, 0)


def _summary_length(rows: list[dict], max_len: int) -> CheckResult:
    longest = max((len(r["extrait"] or "") for r in rows), default=0)
    return CheckResult("C04_extrait_borne", longest <= max_len, longest, max_len)


def _encoding(rows: list[dict]) -> CheckResult:
    bad = [r for r in rows if has_mojibake(r["titre"]) or has_mojibake(r["extrait"] or "")]
    return CheckResult(
        "C05_encodage_sain", not bad, len(bad), 0, ", ".join(r["titre"][:40] for r in bad[:3])
    )


def _reject_rate(report: dict, max_rate: float) -> CheckResult:
    sources = report.get("par_source", {})
    over = {s: i["taux_rejet"] for s, i in sources.items() if i["taux_rejet"] > max_rate}
    worst = max((i["taux_rejet"] for i in sources.values()), default=0.0)
    detail = ", ".join(f"{s} {r:.0%}" for s, r in sorted(over.items(), key=lambda x: -x[1]))
    return CheckResult("C06_taux_rejet_par_source", not over, worst, max_rate, detail)


def _volume(rows: list[dict], minimum: int) -> CheckResult:
    return CheckResult("C07_volume_minimum", len(rows) >= minimum, len(rows), minimum)


def _sources(rows: list[dict], minimum: int) -> CheckResult:
    present = sorted({r["source_slug"] for r in rows})
    return CheckResult(
        "C08_sources_representees",
        len(present) >= minimum,
        len(present),
        minimum,
        ", ".join(present),
    )


def run_checks(
    rows: list[dict], report: dict, settings: dict, extrait_max: int, now: datetime
) -> list[CheckResult]:
    """`rows` : articles_propres du jour ; `report` : rapport du passage ; `settings` :
    section [controles] de regles.toml ; `now` : heure du passage (UTC)."""
    return [
        _future(rows, now, int(settings.get("tolerance_future_minutes", 60))),
        _empty_titles(rows),
        _duplicate_links(rows),
        _summary_length(rows, extrait_max),
        _encoding(rows),
        _reject_rate(report, float(settings.get("taux_rejet_max_par_source", 0.8))),
        _volume(rows, int(settings.get("articles_min", 1))),
        _sources(rows, int(settings.get("sources_min", 1))),
    ]
