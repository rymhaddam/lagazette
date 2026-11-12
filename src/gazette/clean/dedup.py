"""Dédoublonnage : exact (même source), dépêche reprise (autre source), approximatif (Jaccard).

Les articles sont parcourus du plus ancien au plus récent : la première publication est
gardée, les suivantes sont écartées en citant l'article gardé auquel elles ressemblent.
"""

from __future__ import annotations

from collections import defaultdict

from gazette.clean.models import Article, Rejet, RulesConfig
from gazette.clean.normalize import jaccard, normalize_title
from gazette.collect.parser import canonical_url as canonical_link

DEDUP_RULES = ("R08_doublon_exact", "R10_depeche_reprise", "R09_doublon_approx")


def _order_key(article: Article) -> tuple:
    published = article.publie_le.isoformat() if article.publie_le else ""
    return (published, article.source_slug, article.id)


def deduplicate(articles: list[Article], config: RulesConfig) -> tuple[list[Article], list[Rejet]]:
    enabled = set(config.rule_ids)
    reprise_cfg = config.rule("R10_depeche_reprise")
    approx_cfg = config.rule("R09_doublon_approx")
    extrait_min = int(reprise_cfg.get("extrait_min", 60))
    seuil = float(approx_cfg.get("seuil_jaccard", 0.75))
    mots_min = int(approx_cfg.get("mots_min", 4))
    stopwords = config.stopwords

    kept: list[Article] = []
    rejects: list[Rejet] = []
    by_link: dict[str, Article] = {}
    by_title: dict[str, list[Article]] = defaultdict(list)
    by_summary: dict[str, Article] = {}
    words_index: list[tuple[frozenset[str], Article]] = []

    for article in sorted(articles, key=_order_key):
        title = article.titre_normalise
        verdict: tuple[str, str] | None = None

        # R08 : même lien (toutes sources) ou même titre dans la même source.
        if "R08_doublon_exact" in enabled:
            twin = by_link.get(canonical_link(article.lien))
            if twin is None:
                twin = next(
                    (a for a in by_title[title] if a.source_slug == article.source_slug), None
                )
            if twin is not None:
                verdict = ("R08_doublon_exact", f"doublon de {twin.source_slug}:{twin.id[:10]}")

        # R10 : même titre ou même extrait (assez long) dans une autre source.
        if verdict is None and "R10_depeche_reprise" in enabled:
            twin = next((a for a in by_title[title] if a.source_slug != article.source_slug), None)
            summary_key = normalize_title(article.extrait)
            if twin is None and len(summary_key) >= extrait_min:
                candidate = by_summary.get(summary_key)
                if candidate and candidate.source_slug != article.source_slug:
                    twin = candidate
            if twin is not None:
                verdict = ("R10_depeche_reprise", f"reprise de {twin.source} ({twin.id[:10]})")

        # R09 : titre quasi identique, quelle que soit la source.
        words = frozenset(w for w in title.split() if len(w) > 1 and w not in stopwords)
        if verdict is None and "R09_doublon_approx" in enabled and len(words) >= mots_min:
            best, best_score = None, 0.0
            for other_words, other in words_index:
                score = jaccard(words, other_words)
                if score > best_score:
                    best, best_score = other, score
            if best is not None and best_score >= seuil:
                verdict = (
                    "R09_doublon_approx",
                    f"Jaccard {best_score:.2f} avec {best.source_slug}:{best.id[:10]}",
                )

        if verdict:
            rejects.append(Rejet(article=article, regle_id=verdict[0], raison=verdict[1]))
            continue

        kept.append(article)
        by_link[canonical_link(article.lien)] = article
        by_title[title].append(article)
        summary_key = normalize_title(article.extrait)
        if len(summary_key) >= extrait_min:
            by_summary.setdefault(summary_key, article)
        if len(words) >= mots_min:
            words_index.append((words, article))

    return kept, rejects
