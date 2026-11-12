"""Règles d'exclusion « une à une » (structure et contenu).

Chaque règle est une fonction `(article, maintenant) -> raison | None`, construite à partir
de config/regles.toml et identifiée par un identifiant stable (R01_publi…). Les règles de
doublons, qui comparent les articles entre eux, sont dans `dedup.py`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta

from gazette.clean.models import Article, RulesConfig
from gazette.clean.normalize import has_mojibake

RuleFn = Callable[[Article, datetime], str | None]

# Ordre d'évaluation : la première règle qui vise un article l'écarte.
STRUCTURE_RULES = (
    "R13_lien_invalide",
    "R05_titre_vide",
    "R07_encodage_casse",
    "R12_date_absente",
    "R06_date_future",
    "R11_trop_ancien",
)
CONTENT_RULES = ("R01_publi", "R02_horoscope", "R03_programme_tv", "R04_jeux_concours")


# -- structure ----------------------------------------------------------------
def _lien_invalide(cfg: dict) -> RuleFn:
    def rule(article: Article, now: datetime) -> str | None:
        if not article.lien.lower().startswith(("http://", "https://")):
            return f"lien non HTTP(S) : {article.lien[:60]!r}"
        if article.lien.lower().count("://") > 1:
            return "lien malformé (hôte doublé non réparable)"
        return None

    return rule


def _titre_vide(cfg: dict) -> RuleFn:
    min_len = int(cfg.get("longueur_min", 1))

    def rule(article: Article, now: datetime) -> str | None:
        if not article.titre:
            return "titre vide"
        if len(article.titre) < min_len:
            return f"titre de {len(article.titre)} caractères (< {min_len})"
        return None

    return rule


def _encodage_casse(cfg: dict) -> RuleFn:
    def rule(article: Article, now: datetime) -> str | None:
        for field in ("titre", "extrait"):
            if has_mojibake(getattr(article, field)):
                return f"encodage cassé dans le {field}"
        return None

    return rule


def _date_absente(cfg: dict) -> RuleFn:
    def rule(article: Article, now: datetime) -> str | None:
        return "date de publication absente ou illisible" if article.publie_le is None else None

    return rule


def _date_future(cfg: dict) -> RuleFn:
    tolerance = timedelta(minutes=int(cfg.get("tolerance_minutes", 0)))

    def rule(article: Article, now: datetime) -> str | None:
        if article.publie_le and article.publie_le > now + tolerance:
            return f"publié le {article.publie_le.isoformat()}, après le passage"
        return None

    return rule


def _trop_ancien(cfg: dict) -> RuleFn:
    max_age = timedelta(hours=int(cfg.get("age_max_heures", 72)))

    def rule(article: Article, now: datetime) -> str | None:
        if article.publie_le and now - article.publie_le > max_age:
            days = (now - article.publie_le).days
            return f"publié il y a {days} j (> {max_age.total_seconds() / 3600:.0f} h)"
        return None

    return rule


# -- contenu (rubriques + motifs) -------------------------------------------
def _content_rule(cfg: dict) -> RuleFn:
    sections = frozenset(s.lower() for s in cfg.get("rubriques", []))
    patterns = [re.compile(p, re.IGNORECASE) for p in cfg.get("motifs", [])]

    def rule(article: Article, now: datetime) -> str | None:
        hit = sections & article.sections
        if hit:
            return f"rubrique « {sorted(hit)[0]} »"
        for field in ("titre", "extrait"):
            text = getattr(article, field)
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    return f"motif « {match.group(0)} » dans le {field}"
        return None

    return rule


_BUILDERS: dict[str, Callable[[dict], RuleFn]] = {
    "R13_lien_invalide": _lien_invalide,
    "R05_titre_vide": _titre_vide,
    "R07_encodage_casse": _encodage_casse,
    "R12_date_absente": _date_absente,
    "R06_date_future": _date_future,
    "R11_trop_ancien": _trop_ancien,
    **dict.fromkeys(CONTENT_RULES, _content_rule),
}


class RuleSet:
    """Les règles individuelles, dans l'ordre d'évaluation, prêtes à être appliquées."""

    def __init__(self, config: RulesConfig):
        self.config = config
        self.rules: list[tuple[str, RuleFn]] = [
            (rule_id, _BUILDERS[rule_id](config.rule(rule_id)))
            for rule_id in (*STRUCTURE_RULES, *CONTENT_RULES)
            if rule_id in config.rule_ids  # une règle retirée du TOML est désactivée
        ]

    def first_match(self, article: Article, now: datetime) -> tuple[str, str] | None:
        """(identifiant, raison) de la première règle qui écarte l'article, sinon None."""
        for rule_id, rule in self.rules:
            reason = rule(article, now)
            if reason:
                return rule_id, reason
        return None
