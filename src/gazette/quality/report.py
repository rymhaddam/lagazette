"""Rapport de qualité d'un passage : entrés, gardés, rejetés par règle et par source, top 5.

Le rapport doit permettre de repérer une règle trop gourmande : pour chaque règle, on
voit combien elle écarte, chez qui, et un exemple concret.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from gazette.clean.models import RulesConfig
from gazette.clean.pipeline import CleanResult
from gazette.storage.atomic import atomic_write_json, atomic_write_text, read_json

REPORT_JSON = "rapport.json"
REPORT_MD = "rapport.md"


def _rate(rejected: int, total: int) -> float:
    return round(rejected / total, 4) if total else 0.0


def build_report(result: CleanResult, config: RulesConfig) -> dict:
    per_rule = Counter(r.regle_id for r in result.rejets)
    per_source_rule: dict[str, Counter] = defaultdict(Counter)
    examples: dict[tuple[str, str], str] = {}
    for r in result.rejets:
        per_source_rule[r.article.source_slug][r.regle_id] += 1
        examples.setdefault(
            (r.regle_id, r.article.source_slug), f"{r.article.titre[:90]} — {r.raison}"
        )
    kept_per_source = Counter(a.source_slug for a in result.gardes)

    sources = {}
    for slug, entered in sorted(result.entrees_par_source.items()):
        rejected = sum(per_source_rule[slug].values())
        sources[slug] = {
            "source": result.noms_sources.get(slug, slug),
            "entrees": entered,
            "gardes": kept_per_source[slug],
            "rejetes": rejected,
            "taux_rejet": _rate(rejected, entered),
            "par_regle": dict(per_source_rule[slug].most_common()),
        }

    pairs = Counter((r.regle_id, r.article.source_slug) for r in result.rejets)
    top = [
        {"regle_id": rule_id, "source": slug, "nombre": n, "exemple": examples[(rule_id, slug)]}
        for (rule_id, slug), n in pairs.most_common(5)
    ]

    return {
        "jour_collecte": result.jour_collecte.isoformat(),
        "passage_le": result.passage_le.isoformat(),
        "regles": str(config.path) if config.path else None,
        "entrees": result.entrees,
        "gardes": len(result.gardes),
        "rejetes": len(result.rejets),
        "taux_rejet": _rate(len(result.rejets), result.entrees),
        "par_regle": {
            rule_id: {"nombre": n, "description": config.description(rule_id)}
            for rule_id, n in per_rule.most_common()
        },
        "par_source": sources,
        "top_rejets": top,
        "stockage": None,  # rempli après écriture en base
        "controles": None,  # rempli par l'étape de contrôle
    }


# -- rendu lisible ------------------------------------------------------------
def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def render_markdown(report: dict) -> str:
    lines = [
        f"# Rapport de qualité — {report['jour_collecte']}",
        "",
        f"Passage du {report['passage_le']} (UTC).",
        "",
        f"**{report['entrees']}** items entrés, **{report['gardes']}** gardés, "
        f"**{report['rejetes']}** rejetés ({_pct(report['taux_rejet'])}).",
    ]
    if report.get("stockage"):
        s = report["stockage"]
        lines.append(
            f"En base : {s['inseres']} nouveaux articles, {s['deja_connus']} déjà publiés un "
            f"autre jour, {s['rejets']} rejets tracés."
        )

    lines += [
        "",
        "## Rejets par règle",
        "",
        "| Règle | Rejets | Ce qu'elle écarte |",
        "|---|---:|---|",
    ]
    for rule_id, info in report["par_regle"].items():
        lines.append(f"| `{rule_id}` | {info['nombre']} | {info['description']} |")
    if not report["par_regle"]:
        lines.append("| — | 0 | aucun rejet |")

    lines += [
        "",
        "## Par source",
        "",
        "| Source | Entrés | Gardés | Rejetés | Taux | Détail |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for info in report["par_source"].values():
        detail = ", ".join(f"{k} {v}" for k, v in info["par_regle"].items()) or "—"
        lines.append(
            f"| {info['source']} | {info['entrees']} | {info['gardes']} | {info['rejetes']} "
            f"| {_pct(info['taux_rejet'])} | {detail} |"
        )

    lines += ["", "## Les cinq rejets les plus fréquents", ""]
    for i, top in enumerate(report["top_rejets"], start=1):
        lines.append(
            f"{i}. `{top['regle_id']}` chez **{top['source']}** : {top['nombre']} "
            f"— ex. « {top['exemple']} »"
        )
    if not report["top_rejets"]:
        lines.append("Aucun rejet.")

    lines += ["", "## Contrôles qualité", ""]
    checks = report.get("controles")
    if checks is None:
        lines.append("Contrôles pas encore exécutés pour ce passage.")
    else:
        lines += ["| Contrôle | Résultat | Valeur | Seuil | Détail |", "|---|---|---:|---:|---|"]
        for c in checks:
            status = "✅ ok" if c["ok"] else "❌ KO"
            lines.append(
                f"| `{c['controle']}` | {status} | {c['valeur']} | {c['seuil']} | {c['detail']} |"
            )
    return "\n".join(lines) + "\n"


# -- fichiers -----------------------------------------------------------------
def report_dir(quality_dir: Path, day: str) -> Path:
    return Path(quality_dir) / day


def write_report(quality_dir: Path, report: dict) -> Path:
    """Écrit data/quality/<jour>/rapport.json et rapport.md. Renvoie le dossier."""
    folder = report_dir(quality_dir, report["jour_collecte"])
    atomic_write_json(folder / REPORT_JSON, report)
    atomic_write_text(folder / REPORT_MD, render_markdown(report))
    return folder


def read_report(quality_dir: Path, day: str) -> dict | None:
    return read_json(report_dir(quality_dir, day) / REPORT_JSON)
