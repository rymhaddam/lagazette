-- Tables du nettoyage et de la qualité (TD 4), pour une installation neuve.
-- Sur un volume existant, ce script ne s'exécute pas : le paquet gazette crée les mêmes
-- tables au premier `gazette clean` (CREATE TABLE IF NOT EXISTS, src/gazette/storage/db.py).
\connect gazette

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
