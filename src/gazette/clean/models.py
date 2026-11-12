"""Modèles du nettoyage : article normalisé, rejet, configuration des règles."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict


class Article(BaseModel):
    """Un item brut normalisé : ce qui sera stocké dans `articles_propres`.

    On ne garde que ce qu'on a le droit de republier : titre, extrait court, lien, date,
    source. Aucune donnée personnelle (le brut n'en contient déjà pas).
    """

    model_config = ConfigDict(frozen=True)

    id: str  # identifiant de l'item brut (sha256 de l'URL canonique) : traçabilité
    source: str
    source_slug: str
    source_type: str
    titre: str
    extrait: str
    lien: str
    publie_le: datetime | None  # UTC
    collecte_le: datetime | None  # UTC
    jour_collecte: date
    titre_normalise: str  # sans accents, ponctuation ni préfixe éditorial : pour comparer
    sections: frozenset[str] = frozenset()  # segments de l'URL (rubriques)

    def to_row(self) -> dict[str, Any]:
        return self.model_dump(exclude={"sections"})


class Rejet(BaseModel):
    """Un item écarté, avec la règle qui l'a écarté et pourquoi."""

    model_config = ConfigDict(frozen=True)

    article: Article
    regle_id: str
    raison: str


@dataclass(frozen=True)
class RulesConfig:
    """Contenu de config/regles.toml, avec des accès typés."""

    raw: dict[str, Any]
    path: Path | None = None
    _rules: dict[str, dict[str, Any]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_rules", self.raw.get("regles", {}))

    # -- normalisation ------------------------------------------------------
    @property
    def extrait_max(self) -> int:
        return int(self.raw["normalisation"]["extrait_max"])

    @property
    def default_tz(self) -> ZoneInfo:
        return ZoneInfo(self.raw["normalisation"].get("fuseau_par_defaut", "Europe/Paris"))

    @property
    def title_prefixes(self) -> frozenset[str]:
        return frozenset(self.raw["normalisation"].get("prefixes_titre", []))

    @property
    def stopwords(self) -> frozenset[str]:
        return frozenset(self.raw["normalisation"].get("mots_vides", []))

    # -- règles et contrôles ------------------------------------------------
    def rule(self, rule_id: str) -> dict[str, Any]:
        return self._rules.get(rule_id, {})

    def description(self, rule_id: str) -> str:
        return self.rule(rule_id).get("description", rule_id)

    @property
    def rule_ids(self) -> list[str]:
        return list(self._rules)

    @property
    def checks(self) -> dict[str, Any]:
        return self.raw.get("controles", {})


def load_rules_config(path: str | Path) -> RulesConfig:
    path = Path(path)
    with open(path, "rb") as fh:
        return RulesConfig(raw=tomllib.load(fh), path=path)
