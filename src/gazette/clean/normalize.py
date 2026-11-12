"""Normalisation des items bruts : une fonction pure par transformation.

Aucune de ces fonctions ne lit de fichier, d'horloge ou de réglage global : tout ce dont
elles ont besoin est passé en argument, ce qui les rend triviales à tester.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import UTC, datetime, tzinfo
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

# -- HTML -------------------------------------------------------------------
_BLOCK_TAGS = frozenset({"br", "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "tr"})
_SKIP_TAGS = frozenset({"script", "style"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def strip_html(text: str) -> str:
    """Retire les balises (et le contenu de <script>/<style>), décode les entités au passage.

    '<p>Le <b>maire</b> a dit&nbsp;:</p>' -> ' Le maire a dit\xa0: '
    """
    if not text or "<" not in text:
        return text or ""
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


# -- entités et espaces -----------------------------------------------------
def unescape_entities(text: str) -> str:
    """Décode les entités HTML, y compris doublement encodées ('&amp;eacute;' -> 'é')."""
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_SPACES = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    """Espaces insécables et caractères invisibles retirés, blancs écrasés, bords nettoyés."""
    text = text.translate(_INVISIBLE)
    text = unicodedata.normalize("NFC", text)
    return _SPACES.sub(" ", text).strip()


# -- encodage ---------------------------------------------------------------
# Séquences typiques d'un texte UTF-8 relu en latin-1/cp1252 : 'Ã©' (é), 'Ã¨' (è),
# 'â€™' (’), 'Â ' (espace insécable)...
_MOJIBAKE = re.compile(
    r"Ã[\x80-\xbf€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ©¨ª«®¯´¹º»¼½¾¢£¤¥¦§]|â€|Â[\xa0-\xbf]|�"
)


def has_mojibake(text: str) -> bool:
    """Vrai si le texte contient un encodage cassé reconnaissable (ou le caractère '�')."""
    return bool(_MOJIBAKE.search(text or ""))


def fix_encoding(text: str) -> str:
    """Répare un texte UTF-8 relu en cp1252/latin-1 ('Ã©tÃ©' -> 'été').

    Ne touche pas à un texte sain. Si la réparation échoue, le texte est rendu tel quel :
    la règle R07 l'écartera ensuite.
    """
    if not has_mojibake(text):
        return text
    for codec in ("cp1252", "latin-1"):
        try:
            repaired = text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if not has_mojibake(repaired):
            return repaired
    return text


# -- dates ------------------------------------------------------------------
def to_utc(value: str | None, default_tz: tzinfo) -> datetime | None:
    """Date RFC 822 ('Tue, 21 Oct 2026 07:00:00 +0200') ou ISO 8601 -> datetime UTC.

    Une date sans fuseau est lue dans `default_tz` (Europe/Paris pour la presse française).
    Renvoie None si la date est absente ou illisible.
    """
    if not value or not value.strip():
        return None
    value = value.strip()
    parsed: datetime | None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(UTC).replace(microsecond=0)


# -- longueur ---------------------------------------------------------------
def truncate(text: str, max_len: int) -> str:
    """Borne la longueur, en coupant sur une fin de mot et en ajoutant '…'."""
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-–—") + "…"


# -- titres (pour le dédoublonnage) ------------------------------------------
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    """Forme canonique d'un titre : minuscules, sans accents ni ponctuation.

    'Été : la canicule s’installe !' -> 'ete la canicule s installe'
    """
    # Apostrophes typographiques : séparateurs ('l’usine' -> 'l usine'), pas supprimées.
    title = re.sub(r"[’‘ʼ`´]", " ", title)
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    return _NON_WORD.sub(" ", ascii_title.lower()).strip()


def title_words(title: str, stopwords: frozenset[str] = frozenset()) -> frozenset[str]:
    """Ensemble des mots d'un titre normalisé (hors mots vides et mots d'une lettre)."""
    return frozenset(w for w in normalize_title(title).split() if len(w) > 1 and w not in stopwords)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """|A ∩ B| / |A ∪ B| ; 0 si les deux ensembles sont vides."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


_PREFIX = re.compile(r"^\s*([^.:|,\-–—]{2,25}?)\s*[.:|,\-–—]\s+(?=\S)")


def strip_title_prefix(title: str, prefixes: frozenset[str]) -> str:
    """Retire un préfixe éditorial ('DIRECT. ', 'Météo. ', 'Quiz - ') du début d'un titre.

    Un préfixe est retiré s'il figure dans `prefixes` (comparé sous forme normalisée) ou
    s'il est écrit tout en majuscules. Sert à comparer les titres, pas à les afficher.
    """
    for _ in range(2):  # 'EN DIRECT. VIDÉO. ...'
        match = _PREFIX.match(title)
        if not match:
            break
        head = match.group(1).strip()
        if normalize_title(head) in prefixes or (head.isupper() and len(head) > 2):
            title = title[match.end() :]
        else:
            break
    return title


# -- liens -------------------------------------------------------------------
_DOUBLED_SCHEME = re.compile(r"^https?://[^/]*?(https?://)", re.I)


def repair_link(link: str) -> str:
    """Répare un lien dont l'hôte a été doublé par le flux.

    'https://www.nouvelobs.comhttps://www.nouvelobs.com/videos/x' -> 'https://www.nouvelobs.com/videos/x'
    """
    link = (link or "").strip()
    match = _DOUBLED_SCHEME.match(link)
    return link[match.start(1) :] if match else link


def url_sections(link: str) -> frozenset[str]:
    """Segments du chemin de l'URL, en minuscules : sert à reconnaître une rubrique.

    'https://www.lefigaro.fr/bons-plans/maison/x.html' -> {'bons-plans', 'maison', 'x.html'}
    """
    return frozenset(seg.lower() for seg in urlsplit(link).path.split("/") if seg)


def clean_text(text: str) -> str:
    """Enchaînement complet pour un titre ou un extrait : encodage, HTML, entités, espaces."""
    return normalize_whitespace(unescape_entities(strip_html(fix_encoding(text or ""))))
