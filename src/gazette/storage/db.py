"""Stockage du propre dans PostgreSQL (base `gazette`) avec psycopg 3.

Tables (créées par le code, `CREATE TABLE IF NOT EXISTS` : les scripts db/init ne tournent
qu'à la création du volume, pas sur une base existante) :

- articles_propres   un article publiable par ligne (clé : id de l'item brut) ;
- articles_rejetes   chaque item écarté, avec sa règle et sa raison (traçabilité) ;
- metriques_qualite  le résultat chiffré de chaque contrôle, à chaque passage (TD 8) ;
- rapports_qualite   le rapport complet de chaque passage (JSONB).

Rejouer le nettoyage d'un jour est idempotent : on remplace les lignes de ce jour.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from gazette.clean.models import Article, Rejet

log = logging.getLogger(__name__)

# Secondes : sans délai, une base injoignable bloquerait la tâche indéfiniment.
CONNECT_TIMEOUT = 10

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles_propres (
    id              text PRIMARY KEY,
    source          text        NOT NULL,
    source_slug     text        NOT NULL,
    source_type     text        NOT NULL,
    titre           text        NOT NULL,
    extrait         text        NOT NULL DEFAULT '',
    lien            text        NOT NULL,
    publie_le       timestamptz NOT NULL,
    collecte_le     timestamptz,
    jour_collecte   date        NOT NULL,
    titre_normalise text        NOT NULL,
    insere_le       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS articles_propres_jour_idx ON articles_propres (jour_collecte);
CREATE INDEX IF NOT EXISTS articles_propres_source_idx ON articles_propres (source_slug);

CREATE TABLE IF NOT EXISTS articles_rejetes (
    jour_collecte date        NOT NULL,
    id            text        NOT NULL,
    source_slug   text        NOT NULL,
    regle_id      text        NOT NULL,
    raison        text        NOT NULL,
    titre         text        NOT NULL,
    lien          text        NOT NULL,
    publie_le     timestamptz,
    rejete_le     timestamptz NOT NULL,
    PRIMARY KEY (jour_collecte, id)
);
CREATE INDEX IF NOT EXISTS articles_rejetes_regle_idx ON articles_rejetes (jour_collecte, regle_id);

CREATE TABLE IF NOT EXISTS metriques_qualite (
    jour_collecte date             NOT NULL,
    passage_le    timestamptz      NOT NULL,
    controle      text             NOT NULL,
    valeur        double precision,
    seuil         double precision,
    ok            boolean          NOT NULL,
    detail        text             NOT NULL DEFAULT '',
    PRIMARY KEY (jour_collecte, passage_le, controle)
);

CREATE TABLE IF NOT EXISTS rapports_qualite (
    jour_collecte date        NOT NULL,
    passage_le    timestamptz NOT NULL,
    rapport       jsonb       NOT NULL,
    PRIMARY KEY (jour_collecte, passage_le)
);
"""


class DatabaseError(RuntimeError):
    """Base non configurée ou injoignable."""


def ensure_database(url: str) -> None:
    """Crée la base cible si elle n'existe pas (volume Postgres antérieur à db/init)."""
    params = conninfo_to_dict(url)
    dbname = params.get("dbname")
    if not dbname:
        return
    admin = {**params, "dbname": "postgres"}
    with psycopg.connect(**admin, autocommit=True, connect_timeout=CONNECT_TIMEOUT) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if not exists:
            log.warning("Base %s absente : création", dbname)
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))


@contextmanager
def connect(url: str | None) -> Iterator[psycopg.Connection]:
    """Connexion (transaction validée à la sortie) avec schéma garanti."""
    if not url:
        raise DatabaseError("GAZETTE_DATABASE_URL n'est pas défini (voir .env.example).")
    try:
        ensure_database(url)
        conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT)
    except psycopg.OperationalError as exc:
        raise DatabaseError(f"PostgreSQL injoignable : {exc}") from exc
    with conn:
        conn.execute(SCHEMA_SQL)
        yield conn


# -- écriture -----------------------------------------------------------------
_ARTICLE_COLUMNS = (
    "id",
    "source",
    "source_slug",
    "source_type",
    "titre",
    "extrait",
    "lien",
    "publie_le",
    "collecte_le",
    "jour_collecte",
    "titre_normalise",
)


def replace_day(
    conn: psycopg.Connection,
    day: date,
    kept: Iterable[Article],
    rejects: Iterable[Rejet],
    rejected_at: datetime,
) -> dict[str, int]:
    """Remplace le propre et les rejets du jour. Un article déjà publié un autre jour
    (même id, le flux le republie) n'est pas réinséré : il garde son jour d'origine.

    Renvoie {'inseres': n, 'deja_connus': m, 'rejets': r}.
    """
    kept = list(kept)
    rejects = list(rejects)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM articles_propres WHERE jour_collecte = %s", (day,))
        cur.execute("DELETE FROM articles_rejetes WHERE jour_collecte = %s", (day,))
        inserted = 0
        if kept:
            columns = sql.SQL(", ").join(map(sql.Identifier, _ARTICLE_COLUMNS))
            placeholders = sql.SQL(", ").join(sql.Placeholder() * len(_ARTICLE_COLUMNS))
            query = sql.SQL(
                "INSERT INTO articles_propres ({}) VALUES ({}) ON CONFLICT (id) DO NOTHING"
            ).format(columns, placeholders)
            for article in kept:
                row = article.to_row()
                cur.execute(query, [row[c] for c in _ARTICLE_COLUMNS])
                inserted += cur.rowcount
        if rejects:
            cur.executemany(
                """INSERT INTO articles_rejetes
                   (jour_collecte, id, source_slug, regle_id, raison, titre, lien, publie_le,
                    rejete_le)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (jour_collecte, id) DO NOTHING""",
                [
                    (
                        day,
                        r.article.id,
                        r.article.source_slug,
                        r.regle_id,
                        r.raison,
                        r.article.titre,
                        r.article.lien,
                        r.article.publie_le,
                        rejected_at,
                    )
                    for r in rejects
                ],
            )
    return {"inseres": inserted, "deja_connus": len(kept) - inserted, "rejets": len(rejects)}


def save_metrics(
    conn: psycopg.Connection, day: date, passage_le: datetime, checks: Iterable[dict]
) -> None:
    conn.cursor().executemany(
        """INSERT INTO metriques_qualite
           (jour_collecte, passage_le, controle, valeur, seuil, ok, detail)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (jour_collecte, passage_le, controle) DO UPDATE
           SET valeur = EXCLUDED.valeur, seuil = EXCLUDED.seuil, ok = EXCLUDED.ok,
               detail = EXCLUDED.detail""",
        [
            (day, passage_le, c["controle"], c["valeur"], c["seuil"], c["ok"], c["detail"])
            for c in checks
        ],
    )


def save_report(conn: psycopg.Connection, day: date, passage_le: datetime, report: dict) -> None:
    conn.execute(
        """INSERT INTO rapports_qualite (jour_collecte, passage_le, rapport)
           VALUES (%s, %s, %s)
           ON CONFLICT (jour_collecte, passage_le) DO UPDATE SET rapport = EXCLUDED.rapport""",
        (day, passage_le, Jsonb(report)),
    )


# -- lecture ------------------------------------------------------------------
def fetch_day(conn: psycopg.Connection, day: date) -> list[dict[str, Any]]:
    """Les articles propres du jour, tels que stockés (les contrôles portent sur eux)."""
    return conn.execute(
        "SELECT * FROM articles_propres WHERE jour_collecte = %s ORDER BY publie_le", (day,)
    ).fetchall()
