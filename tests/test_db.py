"""Stockage PostgreSQL. Sautés si GAZETTE_TEST_DATABASE_URL n'est pas défini.

En local :  GAZETTE_TEST_DATABASE_URL=postgresql://airflow:airflow@127.0.0.1:5432/gazette_test
(la base est créée si besoin ; la CI fournit un service Postgres).
"""

import os
import shutil
from datetime import UTC, date, datetime

import pytest
from conftest import FIXTURES, RULES_FILE

from gazette.quality.runner import run_clean, run_quality_checks

URL = os.environ.get("GAZETTE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GAZETTE_TEST_DATABASE_URL non défini")

FIX_DAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


@pytest.fixture
def db_settings(settings, tmp_path):
    from gazette.storage import db

    with db.connect(URL) as conn:
        for table in (
            "articles_propres",
            "articles_rejetes",
            "metriques_qualite",
            "rapports_qualite",
        ):
            conn.execute(f"TRUNCATE {table}")
    shutil.copytree(FIXTURES / "raw", tmp_path / "raw")
    return settings.model_copy(
        update={"data_dir": tmp_path, "rules_file": RULES_FILE, "database_url": URL}
    )


def test_clean_fills_tables_and_is_idempotent(db_settings):
    from gazette.storage import db

    report, rows = run_clean(db_settings, FIX_DAY, now=NOW)
    assert report["stockage"] == {"inseres": len(rows), "deja_connus": 0, "rejets": 14}

    # Rejouer le même jour ne duplique rien.
    report, _ = run_clean(db_settings, FIX_DAY, now=NOW)
    with db.connect(URL) as conn:
        kept = conn.execute("SELECT count(*) AS n FROM articles_propres").fetchone()["n"]
        rules = conn.execute(
            "SELECT regle_id, count(*) AS n FROM articles_rejetes GROUP BY 1"
        ).fetchall()
    assert kept == len(rows)
    assert {r["regle_id"]: r["n"] for r in rules}["R01_publi"] == 2
    assert sum(r["n"] for r in rules) == 14


def test_article_seen_on_previous_day_keeps_its_day(db_settings):
    run_clean(db_settings, FIX_DAY, now=NOW)
    next_day = date(2026, 10, 22)
    shutil.copytree(db_settings.raw_dir / "2026-10-21", db_settings.raw_dir / next_day.isoformat())
    report, _ = run_clean(db_settings, next_day, now=NOW)
    assert report["stockage"]["inseres"] == 0
    assert report["stockage"]["deja_connus"] == report["gardes"]


def test_checks_publish_metrics(db_settings):
    from gazette.storage import db

    run_clean(db_settings, FIX_DAY, now=NOW)
    summary = run_quality_checks(
        db_settings,
        FIX_DAY,
        overrides={"sources_min": 2, "articles_min": 1, "taux_rejet_max_par_source": 0.9},
    )
    assert summary["ok"]
    with db.connect(URL) as conn:
        metrics = conn.execute("SELECT controle, ok FROM metriques_qualite").fetchall()
        stored = conn.execute("SELECT rapport FROM rapports_qualite").fetchone()["rapport"]
    assert len(metrics) == 8 and all(m["ok"] for m in metrics)
    assert len(stored["controles"]) == 8
