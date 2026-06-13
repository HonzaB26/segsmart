"""Executable i18n contract — bilingual UI is a hard rule, not a hope.

The dashboard and the setup page are fully bilingual (EN/CS). Every time a
contributor adds UI without wiring it into BOTH language dicts, the Czech
demo breaks silently — it happened twice. So the contract is a test, the same
way the no-PII and docs-sync rules are enforced rather than merely written
down (see AGENTS.md rule #11).

Three checks per HTML page:
  1. the `en` and `cs` dicts have the *same* keys (no half-translation),
  2. every `data-i="key"` in the markup resolves in both dicts,
  3. no user-visible chrome (button / heading / label / th) ships literal
     text without being wired to a translation key.
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ["index.html", "setup.html"]

# tags that carry user-facing chrome and must be translated
CHROME_TAGS = ("button", "h1", "h2", "h3", "label", "th", "a")
# user-visible attributes (tooltips, screen-reader labels) — also must translate
ATTR_KEYS = ("title", "aria-label", "alt")


def _read(page):
    with open(os.path.join(HERE, page), encoding="utf-8") as f:
        return f.read()


def _markup(src):
    """Just the static HTML, not the <script> block. The JS holds dict values
    that themselves contain markup (e.g. an <a> inside the `empty` message) and
    template literals that build chrome dynamically via t()/tf() — scanning
    those as if they were page markup gives false positives. Static chrome that
    needs `data-i` all lives before the first <script>."""
    return src.split("<script", 1)[0]


def _dict_keys(src, lang):
    """Top-level keys of the `lang:{...}` object literal in a page's `UI` map.

    A small brace/string-aware scan (the dicts nest — months, segnames,
    ext_strength — and string values contain `{`, `:`, quotes), so a regex
    won't do. Returns keys declared at depth 1 only.
    """
    start = src.index("\n" + lang + ":{")
    i = src.index("{", start)
    depth, instr, at_key = 0, None, False
    keys, n = [], len(src)
    while i < n:
        c = src[i]
        if instr:
            if c == "\\":
                i += 2
                continue
            if c == instr:
                instr = None
            i += 1
            continue
        if c in "'\"":
            instr = c
        elif c == "{":
            depth += 1
            if depth == 1:
                at_key = True
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        elif depth == 1 and c == ",":
            at_key = True
        elif depth == 1 and at_key and (c.isalpha() or c == "_"):
            ident = re.match(r"[A-Za-z_]\w*", src[i:]).group(0)
            k = i + len(ident)
            while k < n and src[k] in " \t\n":
                k += 1
            if k < n and src[k] == ":":
                keys.append(ident)
            at_key = False
            i += len(ident)
            continue
        i += 1
    return keys


def _strip_templates(s):
    """Remove `${...}` template expressions — they are wired via t()/tf()."""
    return re.sub(r"\$\{[^}]*\}", "", s)


@pytest.mark.parametrize("page", PAGES)
def test_en_cs_have_identical_keys(page):
    src = _read(page)
    en, cs = set(_dict_keys(src, "en")), set(_dict_keys(src, "cs"))
    assert en == cs, (
        f"{page}: EN/CS dicts diverge — "
        f"only in EN: {sorted(en - cs)}; only in CS: {sorted(cs - en)}. "
        "Every UI string goes into BOTH dicts (AGENTS.md rule #11)."
    )


@pytest.mark.parametrize("page", PAGES)
def test_data_i_keys_resolve_in_both_dicts(page):
    src = _read(page)
    en, cs = set(_dict_keys(src, "en")), set(_dict_keys(src, "cs"))
    used = set(re.findall(r'data-i="([^"]+)"', src))
    # keep only translation-key-shaped values; data-i is reused as a numeric
    # card index in a few templates (data-i="${i}" → "0"), which is not a key
    used = {k for k in used if re.fullmatch(r"[A-Za-z_]\w*", k)}
    missing = sorted(k for k in used if not (k in en and k in cs))
    assert not missing, (
        f"{page}: data-i keys with no dict entry in both languages: {missing}"
    )


@pytest.mark.parametrize("page", PAGES)
def test_no_unwired_chrome_strings(page):
    """Buttons/headings/labels must be wired to a key, not hardcoded text.

    'Wired' = data-i on the element, or a child carrying data-i, or the only
    visible text is a `${...}` template (already going through t()/tf()).
    This is the check that catches the real regression: a contributor adding
    `<button>Score factors</button>` with no translation at all.
    """
    src = _markup(_read(page))
    offenders = []
    elements = [(tag, rf"<{tag}\b([^>]*)>(.*?)</{tag}>") for tag in CHROME_TAGS]
    # hint/help copy lives in <div class="hint"> — user-visible, must translate
    elements.append(("div.hint", r'<div class="hint"([^>]*)>(.*?)</div>'))
    for tag, pat in elements:
        for m in re.finditer(pat, src, re.S):
            attrs, inner = m.group(1), m.group(2)
            if "data-i" in attrs or "data-i" in inner:
                continue
            text = re.sub(r"<[^>]+>", "", _strip_templates(inner)).strip()
            if re.search(r"[A-Za-z]{3,}", text):
                offenders.append(f"<{tag}> {text[:70]!r}")
    assert not offenders, (
        f"{page}: user-visible chrome not wired for translation "
        f"(add data-i + a key in both dicts):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("page", PAGES)
def test_user_visible_attributes_translated(page):
    """`title` / `aria-label` / `alt` show up as tooltips and to screen readers,
    so they need translating too — via `data-i-<attr>` (applyLang sets them from
    a key). A hardcoded `title="UI language"` is just as English-only as a
    hardcoded button label.
    """
    src = _markup(_read(page))
    offenders = []
    for tag_m in re.finditer(r"<[a-zA-Z][^>]*>", src):
        tag = tag_m.group(0)
        for attr in ATTR_KEYS:
            am = re.search(rf'(?<![\w-]){attr}="([^"]*)"', tag)
            if not am:
                continue
            val = am.group(1)
            if "${" in val or not re.search(r"[A-Za-z]{3,}", val):
                continue
            if f"data-i-{attr}=" in tag:    # wired: applyLang sets it from a key
                continue
            name = tag.split(None, 1)[0][1:]
            offenders.append(f"<{name} {attr}={val[:40]!r}>")
    assert not offenders, (
        f"{page}: hardcoded user-visible attributes (wire via data-i-<attr> + a "
        f"key in both dicts):\n  " + "\n  ".join(offenders)
    )
