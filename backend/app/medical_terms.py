"""Medical knowledge base + fuzzy term matcher.

Speech recognition often mangles drug and disease names ("amoxiciline",
"ibuprofene"). The matcher scans every n-gram of a dictation and fuzzy-matches
it against the knowledge base, so misspelled terms are still recognised and can
be replaced by their canonical spelling.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "medical_terms.json"
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
MAX_NGRAM = 5


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def normalize(text: str) -> str:
    """Lowercase, accent-free, punctuation collapsed to single spaces."""
    tokens = TOKEN_RE.findall(strip_accents(text.lower()).replace("œ", "oe"))
    return " ".join(tokens)


@dataclass
class Entity:
    kind: str  # condition | symptom | medication
    en: str
    fr: str
    code: str | None = None
    drug_class: str | None = None
    tags: list[str] = field(default_factory=list)
    aliases: dict[str, list[str]] = field(default_factory=dict)

    def name(self, lang: str) -> str:
        return self.fr if lang == "fr" else self.en

    def to_dict(self, lang: str) -> dict:
        out = {"type": self.kind, "name": self.name(lang), "en": self.en, "fr": self.fr}
        if self.code:
            out["code"] = self.code
        if self.drug_class:
            out["class"] = self.drug_class
        if self.tags:
            out["tags"] = self.tags
        return out


@dataclass
class Match:
    entity: Entity
    start: int  # char offsets in the original text
    end: int
    matched_text: str
    score: float
    exact: bool


class KnowledgeBase:
    def __init__(self, path: Path = DATA_FILE):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.entities: list[Entity] = []
        for c in raw["conditions"]:
            self.entities.append(Entity("condition", c["en"], c["fr"], code=c["code"], aliases=_aliases(c)))
        for s in raw["symptoms"]:
            self.entities.append(Entity("symptom", s["en"], s["fr"], aliases=_aliases(s)))
        for m in raw["medications"]:
            self.entities.append(
                Entity("medication", m["en"], m["fr"], drug_class=m.get("class"), tags=m.get("tags", []), aliases=_aliases(m))
            )
        self.interactions: list[dict] = raw.get("interactions", [])
        self.allergy_groups: dict[str, list[str]] = raw.get("allergy_groups", {})
        self._indexes: dict[str, dict[int, tuple[list[str], list[Entity]]]] = {}
        self._exact: dict[str, dict[str, Entity]] = {}

    # ---------- indexing ----------
    def _index(self, lang: str):
        if lang in self._indexes:
            return self._indexes[lang], self._exact[lang]
        by_len: dict[int, tuple[list[str], list[Entity]]] = {}
        exact: dict[str, Entity] = {}
        for ent in self.entities:
            names = [ent.en, ent.fr] if ent.kind == "medication" else [ent.name(lang)]
            names += ent.aliases.get(lang, []) + ent.aliases.get("any", [])
            if ent.kind == "medication":
                # drug names are near-identical across languages; accept both
                names += ent.aliases.get("en" if lang == "fr" else "fr", [])
            if ent.code:
                names.append(ent.code)
            for n in names:
                key = normalize(n)
                if not key:
                    continue
                exact.setdefault(key, ent)
                n_tok = len(key.split())
                keys, ents = by_len.setdefault(n_tok, ([], []))
                keys.append(key)
                ents.append(ent)
        self._indexes[lang], self._exact[lang] = by_len, exact
        return by_len, exact

    # ---------- matching ----------
    def find(self, text: str, lang: str = "fr", kinds: set[str] | None = None) -> list[Match]:
        """Return non-overlapping entity matches, preferring longer spans."""
        by_len, exact = self._index(lang)
        tokens = [(m.start(), m.end(), normalize(m.group())) for m in TOKEN_RE.finditer(text)]
        tokens = [t for t in tokens if t[2]]
        candidates: list[tuple[int, int, Match]] = []
        for i in range(len(tokens)):
            for n in range(1, MAX_NGRAM + 1):
                if i + n > len(tokens) or n not in by_len:
                    continue
                gram = " ".join(t[2] for t in tokens[i : i + n])
                ent, score, is_exact = None, 0.0, False
                if gram in exact:
                    ent, score, is_exact = exact[gram], 100.0, True
                else:
                    cutoff = _fuzzy_cutoff(gram)
                    if cutoff is not None:
                        keys, ents = by_len[n]
                        best = process.extractOne(gram, keys, scorer=fuzz.ratio, score_cutoff=cutoff)
                        if best:
                            ent, score = ents[best[2]], best[1]
                if ent is None or (kinds and ent.kind not in kinds):
                    continue
                start, end = tokens[i][0], tokens[i + n - 1][1]
                candidates.append((i, i + n, Match(ent, start, end, text[start:end], score, is_exact)))

        candidates.sort(key=lambda c: (-(c[1] - c[0]), -c[2].score))
        taken: set[int] = set()
        result: list[Match] = []
        for a, b, m in candidates:
            if any(k in taken for k in range(a, b)):
                continue
            taken.update(range(a, b))
            result.append(m)
        result.sort(key=lambda m: m.start)
        return result

    def correct_text(self, text: str, lang: str = "fr") -> tuple[str, list[dict]]:
        """Replace fuzzy (misspelled) matches with canonical names."""
        corrections = []
        out, last = [], 0
        for m in self.find(text, lang):
            if m.exact:
                continue
            canonical = m.entity.name(lang)
            if normalize(canonical) == normalize(m.matched_text):
                continue
            out.append(text[last : m.start])
            out.append(canonical)
            last = m.end
            corrections.append({"from": m.matched_text, "to": canonical, "score": round(m.score, 1)})
        out.append(text[last:])
        return "".join(out), corrections

    def search(self, query: str, lang: str = "fr", kind: str | None = None, limit: int = 10) -> list[dict]:
        """Autocomplete: prefix matches first, then fuzzy matches."""
        q = normalize(query)
        if not q:
            return []
        scored: list[tuple[float, Entity]] = []
        for ent in self.entities:
            if kind and ent.kind != kind:
                continue
            names = [ent.en, ent.fr, *(ent.code and [ent.code] or [])]
            names += [a for v in ent.aliases.values() for a in v]
            best = 0.0
            for n in names:
                nn = normalize(n)
                if nn.startswith(q):
                    best = max(best, 200 - len(nn))
                elif q in nn:
                    best = max(best, 150 - len(nn))
                else:
                    best = max(best, fuzz.partial_ratio(q, nn) if len(q) >= 4 else 0)
            if best >= 80:
                scored.append((best, ent))
        scored.sort(key=lambda s: -s[0])
        return [e.to_dict(lang) for _, e in scored[:limit]]

    def lookup_medication(self, name: str, lang: str = "fr") -> Entity | None:
        matches = self.find(name, lang, kinds={"medication"})
        return matches[0].entity if matches else None

    def lookup_condition(self, name: str, lang: str = "fr") -> Entity | None:
        matches = self.find(name, lang, kinds={"condition"})
        if matches and (matches[0].end - matches[0].start) >= 0.6 * len(name.strip()):
            return matches[0].entity
        return None

    def vocabulary_prompt(self, lang: str = "fr", limit: int = 120) -> str:
        """Short list of drug names used to bias the Whisper decoder."""
        meds = [e.name(lang) for e in self.entities if e.kind == "medication"][:limit]
        return ", ".join(meds)

    def stats(self) -> dict:
        counts: dict[str, int] = {}
        for e in self.entities:
            counts[e.kind] = counts.get(e.kind, 0) + 1
        counts["interactions"] = len(self.interactions)
        return counts


def _aliases(raw: dict) -> dict[str, list[str]]:
    return {"en": raw.get("aliases_en", []), "fr": raw.get("aliases_fr", []), "any": raw.get("aliases", [])}


def _fuzzy_cutoff(gram: str) -> float | None:
    """Short strings only match exactly: 'moral' must not become 'Mopral'."""
    n = len(gram)
    if n < 6:
        return None
    if n <= 8:
        return 90
    return 85


@lru_cache(maxsize=1)
def get_kb() -> KnowledgeBase:
    return KnowledgeBase()
