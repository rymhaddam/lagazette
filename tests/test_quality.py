"""Rapport de qualité, contrôles de données et alerte Discord (webhook simulé)."""

import json
import shutil
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from conftest import FIXTURES, RULES_FILE

from gazette.clean.models import load_rules_config
from gazette.clean.pipeline import clean_items, load_raw_day
from gazette.quality.alert import DISCORD_MAX, format_alert, send_alert
from gazette.quality.checks import run_checks
from gazette.quality.report import build_report, render_markdown
from gazette.quality.runner import run_clean, run_quality_checks

FIX_DAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def config():
    return load_rules_config(RULES_FILE)


@pytest.fixture(scope="module")
def result(config):
    return clean_items(load_raw_day(FIXTURES / "raw", FIX_DAY), FIX_DAY, config, NOW)


@pytest.fixture
def quality_settings(settings):
    """Réglages pointant sur le brut de la fixture et sur un webhook factice."""
    return settings.model_copy(
        update={
            "data_dir": FIXTURES,
            "rules_file": RULES_FILE,
            "alert_webhook": "https://discord.test/api/webhooks/1/abc",
        }
    )


# -- rapport --------------------------------------------------------------------
def test_report_counts_per_rule_and_per_source(result, config):
    report = build_report(result, config)
    assert report["entrees"] == 20
    assert report["gardes"] + report["rejetes"] == 20
    assert report["par_regle"]["R01_publi"]["nombre"] == 2
    assert sum(r["nombre"] for r in report["par_regle"].values()) == report["rejetes"]
    a = report["par_source"]["journal-a"]
    assert a["entrees"] == 17 and a["gardes"] + a["rejetes"] == 17
    assert a["taux_rejet"] == round(a["rejetes"] / 17, 4)
    assert len(report["top_rejets"]) == 5
    assert report["top_rejets"][0]["regle_id"] == "R01_publi"  # la plus fréquente
    json.dumps(report)  # sérialisable tel quel (fichier + JSONB)


def test_markdown_report_is_readable(result, config):
    md = render_markdown(build_report(result, config))
    assert "# Rapport de qualité — 2026-10-21" in md
    assert "| `R01_publi` | 2 |" in md
    assert "Journal A" in md
    assert "Contrôles pas encore exécutés" in md


# -- contrôles ------------------------------------------------------------------
def _row(**kw):
    row = {
        "id": "abc",
        "source_slug": "s",
        "titre": "Un titre",
        "extrait": "Un extrait",
        "lien": "https://s.fr/a",
        "publie_le": NOW - timedelta(hours=1),
    }
    return row | kw


def _by_name(results):
    return {c.controle: c for c in results}


def test_checks_pass_on_clean_data(result, config):
    rows = [a.to_row() for a in result.gardes]
    report = build_report(result, config)
    # La fixture est polluée exprès (journal-a rejeté à 82 %) : seuil relevé ici.
    thresholds = {"articles_min": 1, "sources_min": 2, "taux_rejet_max_par_source": 0.9}
    checks = run_checks(rows, report, thresholds, 300, NOW)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert len(checks) == 8


def test_checks_detect_false_facts():
    rows = [
        _row(id="1", publie_le=NOW + timedelta(days=2)),
        _row(id="2", titre="  "),
        _row(id="3", lien="https://s.fr/dup"),
        _row(id="4", lien="https://s.fr/dup", extrait="x" * 400),
        _row(id="5", titre="Ã©tÃ©", lien="https://s.fr/b"),
    ]
    report = {"par_source": {"s": {"taux_rejet": 0.9}, "t": {"taux_rejet": 0.1}}}
    checks = _by_name(run_checks(rows, report, {"articles_min": 10, "sources_min": 2}, 300, NOW))
    assert not checks["C01_aucune_date_future"].ok and checks["C01_aucune_date_future"].valeur == 1
    assert not checks["C02_aucun_titre_vide"].ok
    assert not checks["C03_aucun_doublon_lien"].ok
    assert not checks["C04_extrait_borne"].ok and checks["C04_extrait_borne"].valeur == 400
    assert not checks["C05_encodage_sain"].ok
    rate = checks["C06_taux_rejet_par_source"]
    assert not rate.ok and rate.valeur == 0.9 and "s 90%" in rate.detail
    assert not checks["C07_volume_minimum"].ok
    assert not checks["C08_sources_representees"].ok


def test_reject_rate_threshold_is_inclusive():
    report = {"par_source": {"s": {"taux_rejet": 0.8}}}
    checks = _by_name(run_checks([_row()], report, {"taux_rejet_max_par_source": 0.8}, 300, NOW))
    assert checks["C06_taux_rejet_par_source"].ok


# -- alerte ---------------------------------------------------------------------
def test_format_alert_lists_failed_checks_and_fits_discord():
    failed = [{"controle": "C06_taux_rejet_par_source", "valeur": 0.9, "seuil": 0.8, "detail": "s"}]
    message = format_alert("2026-10-21", failed)
    assert "C06_taux_rejet_par_source" in message and "0.9" in message
    long = format_alert("2026-10-21", failed * 200)
    assert len(long) <= DISCORD_MAX


def test_send_alert_posts_to_webhook():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_alert("https://discord.test/hook", "bonjour", client=client)
    assert sent == [{"content": "bonjour", "username": "La gazette"}]


def test_send_alert_never_raises():
    def down(request):
        raise httpx.ConnectError("injoignable")

    assert not send_alert(
        "https://discord.test/hook", "x", client=httpx.Client(transport=httpx.MockTransport(down))
    )
    refused = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert not send_alert("https://discord.test/hook", "x", client=refused)
    assert not send_alert("", "x")  # pas de webhook : journalisé seulement


# -- enchaînement sans base ------------------------------------------------------
def test_run_clean_and_checks_without_db(quality_settings, tmp_path):
    settings = quality_settings.model_copy(update={"data_dir": tmp_path})
    shutil.copytree(FIXTURES / "raw", tmp_path / "raw")  # brut de la fixture, rapports à part
    report, rows = run_clean(settings, FIX_DAY, use_db=False, now=NOW)
    assert report["gardes"] == len(rows)
    assert (tmp_path / "quality" / "2026-10-21" / "rapport.md").exists()

    calls = []
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(204))
    )
    # Seuils réels : la fixture est petite et polluée -> trois faits faux, une alerte.
    summary = run_quality_checks(settings, FIX_DAY, use_db=False, rows=rows, alert_client=client)
    assert not summary["ok"]
    assert set(summary["echecs"]) == {
        "C06_taux_rejet_par_source",  # journal-a rejeté à 82 %
        "C07_volume_minimum",
        "C08_sources_representees",
    }
    assert summary["alerte_envoyee"] and len(calls) == 1

    # Seuils remplacés pour ce passage : plus rien de faux, pas d'alerte.
    calls.clear()
    summary = run_quality_checks(
        settings,
        FIX_DAY,
        use_db=False,
        rows=rows,
        overrides={"sources_min": 2, "articles_min": 1, "taux_rejet_max_par_source": 0.9},
        alert_client=client,
    )
    assert summary["ok"] and not calls
    md = (tmp_path / "quality" / "2026-10-21" / "rapport.md").read_text(encoding="utf-8")
    assert "✅ ok" in md


def test_checks_require_a_report(quality_settings, tmp_path):
    settings = quality_settings.model_copy(update={"data_dir": tmp_path})
    with pytest.raises(FileNotFoundError):
        run_quality_checks(settings, FIX_DAY, use_db=False, rows=[])
