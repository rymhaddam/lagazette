"""Normalisation : une fonction pure, un test (au moins) par fonction."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from gazette.clean.normalize import (
    clean_text,
    fix_encoding,
    has_mojibake,
    jaccard,
    normalize_title,
    normalize_whitespace,
    repair_link,
    strip_html,
    strip_title_prefix,
    title_words,
    to_utc,
    truncate,
    unescape_entities,
    url_sections,
)

PARIS = ZoneInfo("Europe/Paris")


def test_strip_html_removes_tags_scripts_and_keeps_words_apart():
    html = "<p>Le <b>maire</b> a dit</p><p>non</p><script>track()</script><br/>fin"
    assert normalize_whitespace(strip_html(html)) == "Le maire a dit non fin"
    assert strip_html("sans balise") == "sans balise"
    assert strip_html("") == ""


def test_unescape_entities_handles_double_encoding():
    assert unescape_entities("Caf&eacute; &amp; th&#233;") == "Café & thé"
    assert unescape_entities("&amp;eacute;t&amp;eacute;") == "été"


def test_normalize_whitespace_nbsp_invisible_and_runs():
    assert normalize_whitespace("  «\xa0Oui\xa0»​ :\n\t non  ") == "« Oui » : non"


def test_has_mojibake():
    assert has_mojibake("Ã©tÃ©")
    assert has_mojibake("lâ€™usine")
    assert has_mojibake("tr�s")
    assert not has_mojibake("Été à l’usine, 30 °C")


def test_fix_encoding_repairs_and_leaves_clean_text_alone():
    assert fix_encoding("Ã‰tÃ© indien") == "Été indien"
    assert fix_encoding("lâ€™usine") == "l’usine"
    assert fix_encoding("Été à l’usine") == "Été à l’usine"
    assert fix_encoding("perdu �") == "perdu �"  # irréparable : rendu tel quel


def test_to_utc_rfc822_iso_naive_and_invalid():
    expected = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)
    assert to_utc("Tue, 21 Oct 2026 07:00:00 +0200", PARIS) == expected
    assert to_utc("2026-10-21T05:00:00Z", PARIS) == expected
    assert to_utc("2026-10-21T07:00:00", PARIS) == expected  # sans fuseau : heure de Paris
    assert to_utc("Tue, 21 Oct 2026 05:00:00 GMT", PARIS) == expected
    assert to_utc(None, PARIS) is None
    assert to_utc("hier soir", PARIS) is None


def test_truncate_on_word_boundary():
    text = "un deux trois quatre cinq"
    assert truncate(text, 100) == text
    assert truncate(text, 12) == "un deux…"
    assert len(truncate("x" * 500, 300)) <= 300


def test_normalize_title_accents_punctuation_apostrophes():
    assert normalize_title("Été : la canicule s’installe !") == "ete la canicule s installe"
    assert normalize_title("l’usine") == normalize_title("l'usine") == "l usine"


def test_title_words_drops_stopwords_and_single_letters():
    assert title_words("Le maire de Lyon a démissionné", frozenset({"le", "de"})) == {
        "maire",
        "lyon",
        "demissionne",
    }


def test_jaccard():
    a, b = frozenset("abc"), frozenset("abd")
    assert jaccard(a, b) == 0.5
    assert jaccard(a, a) == 1.0
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_strip_title_prefix_known_or_uppercase_only():
    prefixes = frozenset({"direct", "meteo", "quiz"})
    assert strip_title_prefix("DIRECT. Guerre en Ukraine", prefixes) == "Guerre en Ukraine"
    assert strip_title_prefix("Météo. Neige en vue", prefixes) == "Neige en vue"
    assert strip_title_prefix("TÉMOIGNAGE. Elle raconte", prefixes) == "Elle raconte"
    assert strip_title_prefix("EN DIRECT, VIDÉO. Le match", prefixes) == "Le match"
    # « Guerre en Ukraine » n'est ni connu ni en majuscules : rien n'est retiré.
    assert strip_title_prefix("Guerre en Ukraine : la suite", prefixes) == (
        "Guerre en Ukraine : la suite"
    )


def test_repair_link_doubled_host():
    broken = "https://www.nouvelobs.comhttps://www.nouvelobs.com/videos/x.DMT/a"
    assert repair_link(broken) == "https://www.nouvelobs.com/videos/x.DMT/a"
    assert repair_link(" https://ok.fr/a ") == "https://ok.fr/a"


def test_url_sections():
    assert url_sections("https://www.lefigaro.fr/bons-plans/maison/x.html?a=1") == {
        "bons-plans",
        "maison",
        "x.html",
    }
    assert url_sections("https://www.site.fr") == frozenset()


def test_clean_text_full_chain():
    raw = "<p>Les temp&eacute;ratures\xa0ont <b>d&eacute;pass&eacute;</b>  30 °C</p>"
    assert clean_text(raw) == "Les températures ont dépassé 30 °C"
    assert clean_text("Ã‰tÃ© <i>indien</i>") == "Été indien"
