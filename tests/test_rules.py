"""Règles d'exclusion, testées sur une fixture de brut pollué (tests/fixtures/raw).

Chaque item de la fixture porte le verdict attendu dans `_attendu` : soit « garde », soit
l'identifiant de la règle qui doit l'écarter. On utilise le VRAI config/regles.toml : si
quelqu'un modifie une règle et casse un cas, la CI le voit.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from conftest import FIXTURES, RULES_FILE

from gazette.clean.models import load_rules_config
from gazette.clean.pipeline import clean_items, load_raw_day, normalize_item
from gazette.clean.rules import CONTENT_RULES, STRUCTURE_RULES, RuleSet

FIX_DAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def config():
    return load_rules_config(RULES_FILE)


@pytest.fixture(scope="module")
def result(config):
    return clean_items(load_raw_day(FIXTURES / "raw", FIX_DAY), FIX_DAY, config, NOW)


def _expected() -> dict[str, str]:
    return {
        item["id"]: item["_attendu"]
        for path in (FIXTURES / "raw").rglob("*.json")
        for item in json.loads(path.read_text(encoding="utf-8"))["items"]
    }


def _verdicts(result) -> dict[str, str]:
    verdicts = {a.id: "garde" for a in result.gardes}
    verdicts.update({r.article.id: r.regle_id for r in result.rejets})
    return verdicts


def test_every_fixture_item_gets_its_expected_verdict(result):
    expected, got = _expected(), _verdicts(result)
    assert got == expected


def test_every_rule_is_covered_by_the_fixture(config):
    covered = set(_expected().values()) - {"garde"}
    assert covered == set(config.rule_ids)


def test_counts_add_up(result):
    assert result.entrees == len(result.gardes) + len(result.rejets) == 20
    assert result.entrees_par_source == {"journal-a": 17, "journal-b": 3}


def test_rejections_carry_a_readable_reason(result):
    for rejet in result.rejets:
        assert rejet.raison and rejet.regle_id in {*STRUCTURE_RULES, *CONTENT_RULES, *_dedup()}


def _dedup():
    from gazette.clean.dedup import DEDUP_RULES

    return DEDUP_RULES


def test_kept_articles_are_normalized(result):
    by_title = {a.titre: a for a in result.gardes}
    assert "Été indien : records de chaleur battus dans le Sud-Ouest pour un mois d'octobre" in (
        by_title
    )
    budget = next(a for a in result.gardes if a.titre.startswith("Budget 2027"))
    assert budget.extrait.startswith("Les sénateurs ont voté mardi la partie recettes du budget")
    assert "<" not in budget.extrait
    assert budget.publie_le == datetime(2026, 10, 21, 4, 0, tzinfo=UTC)
    tram = next(a for a in result.gardes if "tramway" in a.titre)
    assert tram.lien == "https://www.journal-a.fr/videos/tramway-montpellier"  # lien réparé
    assert tram.titre_normalise.startswith("la nouvelle ligne")  # préfixe « Vidéo : » retiré


def test_first_publication_is_kept_for_duplicates(result):
    kept_ids = {a.id for a in result.gardes}
    reprise = next(r for r in result.rejets if r.regle_id == "R10_depeche_reprise")
    assert "Journal B" in reprise.raison
    ukraine_b = next(a for a in result.gardes if "Ukraine" in a.titre)
    assert ukraine_b.source_slug == "journal-b" and ukraine_b.id in kept_ids


def test_distinct_articles_on_same_story_are_not_merged(result):
    # Saint-Gaudens (Jaccard 0,38 avec la dépêche Toulouse) reste : regrouper par sujet,
    # c'est le TD 5, pas le nettoyage.
    assert any("Saint-Gaudens" in a.titre for a in result.gardes)


def test_extract_is_bounded(config):
    from gazette.clean.pipeline import RawSource

    source = RawSource("S", "s", "PQN", [])
    item = {
        "id": "x",
        "title": "Un titre parfaitement normal pour un article",
        "summary": "mot " * 500,
        "link": "https://s.fr/a",
        "published": "2026-10-21T06:00:00Z",
        "fetched_at": "2026-10-21T07:00:00+02:00",
    }
    article = normalize_item(item, source, FIX_DAY, config)
    assert len(article.extrait) <= config.extrait_max


def test_rule_removed_from_config_is_disabled(config):
    raw = dict(config.raw)
    raw["regles"] = {k: v for k, v in config.raw["regles"].items() if k != "R02_horoscope"}
    from gazette.clean.models import RulesConfig

    assert "R02_horoscope" not in [rule_id for rule_id, _ in RuleSet(RulesConfig(raw)).rules]


def test_rules_file_in_repo_is_valid():
    cfg = load_rules_config(Path(RULES_FILE))
    assert cfg.extrait_max > 0
    assert 0 < cfg.rule("R09_doublon_approx")["seuil_jaccard"] <= 1
    assert cfg.checks["taux_rejet_max_par_source"] == 0.8
