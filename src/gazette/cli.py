"""Ligne de commande.

python -m gazette collect   [--date AAAA-MM-JJ] [--source SLUG ...] [--strict]
python -m gazette clean     [--date AAAA-MM-JJ] [--sans-base]
python -m gazette check     [--date AAAA-MM-JJ] [--seuil CLE=VALEUR ...]
python -m gazette retention [--apply]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from gazette.collect.runner import run_collection
from gazette.config import ConfigError, Settings, load_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gazette", description="La gazette")
    parser.add_argument("-v", "--verbose", action="store_true", help="journaux détaillés")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="collecte les flux et écrit le stockage brut")
    collect.add_argument(
        "--date",
        type=date.fromisoformat,
        help="jour de collecte AAAA-MM-JJ (défaut : aujourd'hui, heure de Paris)",
    )
    collect.add_argument(
        "--source",
        action="append",
        dest="sources",
        metavar="SLUG",
        help="limiter à une source (option répétable)",
    )
    collect.add_argument(
        "--strict",
        action="store_true",
        help="code retour 1 dès qu'une source échoue (défaut : seulement si toutes échouent)",
    )

    date_help = "jour de collecte AAAA-MM-JJ (défaut : aujourd'hui, heure de Paris)"
    clean = sub.add_parser(
        "clean", help="nettoie le brut du jour, alimente articles_propres, écrit le rapport"
    )
    clean.add_argument("--date", type=date.fromisoformat, help=date_help)
    clean.add_argument(
        "--sans-base",
        action="store_true",
        help="sans PostgreSQL : rapport et contrôles calculés en mémoire, rien n'est stocké",
    )

    check = sub.add_parser("check", help="contrôles qualité sur le propre du jour + alerte")
    check.add_argument("--date", type=date.fromisoformat, help=date_help)
    check.add_argument(
        "--seuil",
        action="append",
        default=[],
        metavar="CLE=VALEUR",
        help="remplace un seuil de [controles] pour ce passage (ex. taux_rejet_max_par_source=0.5)",
    )

    retention = sub.add_parser("retention", help="applique la politique de rétention")
    retention.add_argument("--apply", action="store_true", help="exécute (défaut : simulation)")
    return parser


def _parse_overrides(values: list[str]) -> dict[str, float]:
    overrides = {}
    for value in values:
        key, sep, number = value.partition("=")
        if not sep:
            raise SystemExit(f"--seuil attend CLE=VALEUR, reçu {value!r}")
        overrides[key.strip()] = float(number)
    return overrides


def _print_checks(summary: dict) -> None:
    status = "tous OK" if summary["ok"] else f"ÉCHECS : {', '.join(summary['echecs'])}"
    print(f"Contrôles du {summary['jour_collecte']} : {status}")
    for name, value in summary["metriques"].items():
        print(f"  {name:<28} {value}")
    if not summary["ok"]:
        print(f"  alerte Discord envoyée : {'oui' if summary['alerte_envoyee'] else 'non'}")


def _cmd_clean(settings: Settings, args: argparse.Namespace) -> int:
    from gazette.quality.runner import run_clean, run_quality_checks

    day = args.date or settings.today()
    report, rows = run_clean(settings, day, use_db=not args.sans_base)
    print(
        f"Nettoyage du {day} : {report['entrees']} entrés, {report['gardes']} gardés, "
        f"{report['rejetes']} rejetés ({report['taux_rejet']:.0%})"
    )
    for rule_id, info in report["par_regle"].items():
        print(f"  {rule_id:<22} {info['nombre']}")
    if report.get("stockage"):
        print(f"  en base : {report['stockage']}")
    print(f"Rapport : {settings.quality_dir / day.isoformat() / 'rapport.md'}")
    if args.sans_base:
        _print_checks(run_quality_checks(settings, day, use_db=False, rows=rows))
    return 0


def _cmd_check(settings: Settings, args: argparse.Namespace) -> int:
    from gazette.quality.runner import run_quality_checks

    day = args.date or settings.today()
    summary = run_quality_checks(settings, day, overrides=_parse_overrides(args.seuil))
    _print_checks(summary)
    return 0 if summary["ok"] else 1


def _cmd_retention(settings: Settings, args: argparse.Namespace) -> int:
    from gazette.storage import retention

    today = settings.today()
    actions = (retention.apply if args.apply else retention.plan)(settings, today)
    print(("Rétention appliquée" if args.apply else "Rétention (simulation)") + f" au {today}")
    for action in actions:
        print(f"  {action.kind:<9} {action.target} {action.destination}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )
    # Les journaux httpx (une ligne par requête) sont utiles mais bavards.
    logging.getLogger("httpx").setLevel(logging.INFO if args.verbose else logging.WARNING)

    settings = load_settings()
    if args.command != "collect":
        handler = {"clean": _cmd_clean, "check": _cmd_check, "retention": _cmd_retention}
        return handler[args.command](settings, args)
    try:
        settings.user_agent  # noqa: B018 - vérifie l'e-mail de contact avant tout appel réseau
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2

    manifest = run_collection(settings, args.date, args.sources)

    print(f"\nCollecte du {manifest['collection_date']}")
    for entry in manifest["sources"]:
        line = f"  {entry['status']:<13} {entry['file']:<32} +{entry['new_items']:<4}"
        line += f" total {entry['items_total']}"
        if entry["error"]:
            line += f"  ({entry['error']})"
        print(line)
    summary = manifest["summary"]
    print(
        f"  => {summary['ok']} ok, {summary['not_modified']} non modifiées, "
        f"{summary['failed']} en échec ; {summary['new_items']} nouveaux items."
    )

    if summary["failed"] and (args.strict or summary["failed"] == summary["sources"]):
        return 1
    return 0
