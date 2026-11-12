"""Nettoyage d'un jour de collecte : brut -> normalisation -> règles -> gardés / rejetés.

Le brut (data/raw/<jour>/) est seulement LU : il reste immuable et rejouable. Le cœur
(`clean_items`) est pur : il ne lit ni fichier ni horloge, ce qui le rend testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from gazette.clean.dedup import deduplicate
from gazette.clean.models import Article, Rejet, RulesConfig
from gazette.clean.normalize import (
    clean_text,
    normalize_title,
    repair_link,
    strip_title_prefix,
    to_utc,
    truncate,
    url_sections,
)
from gazette.clean.rules import RuleSet
from gazette.storage.atomic import read_json
from gazette.storage.raw import MANIFEST_NAME

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawSource:
    """Le contenu d'un fichier brut `data/raw/<jour>/<pqr|pqn>/<slug>.json`."""

    name: str
    slug: str
    type: str
    items: list[dict]


@dataclass
class CleanResult:
    jour_collecte: date
    passage_le: datetime  # UTC
    entrees: int
    entrees_par_source: dict[str, int]
    noms_sources: dict[str, str]
    gardes: list[Article] = field(default_factory=list)
    rejets: list[Rejet] = field(default_factory=list)


# -- lecture du brut ----------------------------------------------------------
def load_raw_day(raw_dir: Path, day: date) -> list[RawSource]:
    """Tous les fichiers sources du jour (pqr/ et pqn/), dans un ordre stable."""
    day_dir = Path(raw_dir) / day.isoformat()
    sources = []
    for path in sorted(day_dir.glob("*/*.json")):
        if path.name == MANIFEST_NAME:
            continue
        doc = read_json(path)
        if not doc:
            continue
        sources.append(
            RawSource(
                name=doc.get("source", path.stem),
                slug=doc.get("slug", path.stem),
                type=doc.get("type", path.parent.name.upper()),
                items=doc.get("items", []),
            )
        )
    return sources


# -- normalisation d'un item --------------------------------------------------
def normalize_item(item: dict, source: RawSource, day: date, config: RulesConfig) -> Article:
    """Item brut -> Article : texte nettoyé, lien réparé, dates en UTC, extrait borné."""
    titre = clean_text(item.get("title", ""))
    lien = repair_link(item.get("link", ""))
    return Article(
        id=item["id"],
        source=source.name,
        source_slug=source.slug,
        source_type=source.type,
        titre=titre,
        extrait=truncate(clean_text(item.get("summary", "")), config.extrait_max),
        lien=lien,
        publie_le=to_utc(item.get("published"), config.default_tz),
        collecte_le=to_utc(item.get("fetched_at"), config.default_tz),
        jour_collecte=day,
        titre_normalise=normalize_title(strip_title_prefix(titre, config.title_prefixes)),
        sections=url_sections(lien),
    )


# -- cœur pur -----------------------------------------------------------------
def clean_items(
    sources: list[RawSource], day: date, config: RulesConfig, now: datetime
) -> CleanResult:
    """Applique normalisation, règles individuelles puis dédoublonnage. `now` est en UTC."""
    rules = RuleSet(config)
    result = CleanResult(
        jour_collecte=day,
        passage_le=now,
        entrees=sum(len(s.items) for s in sources),
        entrees_par_source={s.slug: len(s.items) for s in sources},
        noms_sources={s.slug: s.name for s in sources},
    )
    candidates: list[Article] = []
    for source in sources:
        for item in source.items:
            article = normalize_item(item, source, day, config)
            verdict = rules.first_match(article, now)
            if verdict:
                result.rejets.append(Rejet(article=article, regle_id=verdict[0], raison=verdict[1]))
            else:
                candidates.append(article)

    kept, dup_rejects = deduplicate(candidates, config)
    result.gardes = kept
    result.rejets.extend(dup_rejects)
    log.info(
        "Nettoyage du %s : %d entrés, %d gardés, %d rejetés",
        day,
        result.entrees,
        len(result.gardes),
        len(result.rejets),
    )
    return result
