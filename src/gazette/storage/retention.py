"""Politique de rétention (voir docs/qualite.md, section « Conformité et rétention »).

- data/raw/<jour>/            chaud 30 jours, puis compressé en data/archive/raw/<jour>.tar.gz
- data/archive/raw/*.tar.gz   froid jusqu'à 90 jours après la collecte, puis supprimé
- data/state/runs/<jour>/     30 jours (ne sert qu'à construire le manifeste du jour)
- data/quality/<jour>/        chaud 30 jours, puis compressé en data/archive/quality/<jour>.tar.gz
- table articles_rejetes      30 jours (sert à déboguer les règles, pas à publier)

Les éditions publiées (TD suivants) suivront la même logique : plus d'un mois -> froid.
`plan` ne touche à rien ; `apply` exécute le plan.
"""

from __future__ import annotations

import logging
import shutil
import tarfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from gazette.config import Settings

log = logging.getLogger(__name__)

HOT_DAYS = 30
RAW_TOTAL_DAYS = 90
REJECTS_DAYS = 30


@dataclass(frozen=True)
class Action:
    kind: str  # "archive" | "delete" | "purge_db"
    target: str
    destination: str = ""


def _day_dirs(parent: Path) -> list[tuple[date, Path]]:
    found = []
    for path in sorted(parent.glob("????-??-??")):
        try:
            found.append((date.fromisoformat(path.name.removesuffix(".tar.gz")), path))
        except ValueError:
            continue
    return found


def plan(settings: Settings, today: date) -> list[Action]:
    hot_limit = today - timedelta(days=HOT_DAYS)
    raw_limit = today - timedelta(days=RAW_TOTAL_DAYS)
    actions: list[Action] = []
    for name, base in (("raw", settings.raw_dir), ("quality", settings.quality_dir)):
        for day, path in _day_dirs(base):
            if day < hot_limit and path.is_dir():
                dest = settings.archive_dir / name / f"{day.isoformat()}.tar.gz"
                actions.append(Action("archive", str(path), str(dest)))
    for day, path in _day_dirs(settings.state_dir / "runs"):
        if day < hot_limit:
            actions.append(Action("delete", str(path)))
    for archive in sorted((settings.archive_dir / "raw").glob("*.tar.gz")):
        try:
            day = date.fromisoformat(archive.name.removesuffix(".tar.gz"))
        except ValueError:
            continue
        if day < raw_limit:
            actions.append(Action("delete", str(archive)))
    actions.append(Action("purge_db", f"articles_rejetes < {today - timedelta(days=REJECTS_DAYS)}"))
    return actions


def apply(settings: Settings, today: date, *, use_db: bool = True) -> list[Action]:
    actions = plan(settings, today)
    for action in actions:
        if action.kind == "archive":
            dest = Path(action.destination)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp")
            with tarfile.open(tmp, "w:gz") as tar:
                tar.add(action.target, arcname=Path(action.target).name)
            tmp.replace(dest)
            shutil.rmtree(action.target)
        elif action.kind == "delete":
            target = Path(action.target)
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        elif action.kind == "purge_db" and use_db:
            from gazette.storage import db

            with db.connect(settings.database_url) as conn:
                conn.execute(
                    "DELETE FROM articles_rejetes WHERE jour_collecte < %s",
                    (today - timedelta(days=REJECTS_DAYS),),
                )
        log.info("Rétention : %s %s %s", action.kind, action.target, action.destination)
    return actions
