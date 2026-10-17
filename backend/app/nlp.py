"""Rule-based clinical NLP (works offline, no API key needed).

- analyze_diagnosis: detects conditions (ICD-10), symptoms and drugs in dictation.
- parse_prescription: turns "amoxicilline 1 g trois fois par jour pendant 7 jours"
  into a structured prescription line.
- check_safety: allergy + drug-interaction warnings from the knowledge base.
- split_consultation: splits one long dictation into diagnosis / prescription /
  certificate sections using spoken keywords.
"""
from __future__ import annotations

import re
from datetime import date

from .medical_terms import KnowledgeBase, get_kb, normalize

# ---------------------------------------------------------------- numbers
NUMBER_WORDS = {
    # en
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "a single": 1, "half": 0.5,
    # fr
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "quinze": 15, "vingt": 20, "trente": 30,
    "demi": 0.5, "demie": 0.5,
}
_NUM_RE = re.compile(r"\b(" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")\b", re.I)


def _words_to_digits(text: str) -> str:
    def repl(m):
        v = NUMBER_WORDS[m.group(1).lower()]
        return str(int(v)) if v == int(v) else str(v)
    return _NUM_RE.sub(repl, text)


def _fmt_num(s: str) -> str:
    return s.replace(",", ".")


# ---------------------------------------------------------------- patterns
UNIT_MAP = [
    (r"mg|milligrams?|milligrammes?", "mg"),
    (r"mcg|µg|micrograms?|microgrammes?", "µg"),
    (r"g|grams?|grammes?", "g"),
    (r"ml|millilit(?:er|re)s?", "ml"),
    (r"ui|iu|units?|unités?", "IU"),
    (r"%|percent|pour ?cent", "%"),
]
DOSE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(p for p, _ in UNIT_MAP) + r")(?![a-zà-ÿ])", re.I
)

FORMS = {
    # regex -> (en, fr)
    r"tablets?|comprimés?|cp": ("tablet", "comprimé"),
    r"capsules?|gélules?": ("capsule", "gélule"),
    r"sachets?": ("sachet", "sachet"),
    r"puffs?|bouffées?|inhalations?": ("puff", "bouffée"),
    r"drops?|gouttes?": ("drop", "goutte"),
    r"ampoules?|ampules?": ("ampoule", "ampoule"),
    r"suppositor(?:y|ies)|suppositoires?": ("suppository", "suppositoire"),
    r"teaspoons?|cuillères? à café": ("teaspoon", "cuillère à café"),
    r"tablespoons?|cuillères? à soupe": ("tablespoon", "cuillère à soupe"),
    r"injections?|piqûres?": ("injection", "injection"),
    r"applications?": ("application", "application"),
}
STANDALONE_FORMS = {
    r"syrup|sirop": ("syrup", "sirop"),
    r"cream|crème": ("cream", "crème"),
    r"ointment|pommade": ("ointment", "pommade"),
    r"gel": ("gel", "gel"),
    r"nasal spray|spray nasal|spray": ("spray", "spray"),
    r"inhaler|inhalateur": ("inhaler", "inhalateur"),
    r"eye drops|collyre": ("eye drops", "collyre"),
    r"oral solution|solution buvable": ("oral solution", "solution buvable"),
    r"injectable": ("injectable", "injectable"),
}
QTY_FORM_RE = re.compile(
    r"(\d+(?:[.,]\d+)?|½)\s*(" + "|".join(FORMS) + r")\b", re.I
)

FREQUENCIES = [
    # (regex, en template, fr template) — first match wins, specific before generic
    (r"matin,?\s*midi et soir|morning,?\s*noon and (?:evening|night)", "morning, noon and evening", "matin, midi et soir"),
    (r"matin et soir|morning and (?:evening|night)", "morning and evening", "matin et soir"),
    (r"(\d+)\s*(?:times?|fois|x)\s*(?:a|per|par|/)?\s*(?:day|jour|j)\b", "{n} times a day", "{n} fois par jour"),
    (r"(\d+)\s*(?:times?|fois|x)\s*(?:a|per|par|/)?\s*(?:week|semaine)", "{n} times a week", "{n} fois par semaine"),
    (r"\btwice (?:a day|daily)\b|\bbid\b", "2 times a day", "2 fois par jour"),
    (r"\bthrice (?:a day|daily)\b|\btid\b", "3 times a day", "3 fois par jour"),
    (r"every\s*(\d+)\s*hours?|toutes les\s*(\d+)\s*heures?|/\s*(\d+)\s*h\b", "every {n} hours", "toutes les {n} heures"),
    (r"\bonce (?:a day|daily)\b|\bdaily\b|\bevery day\b|\bchaque jour\b|\btous les jours\b|\b1 fois par jour\b",
     "once a day", "1 fois par jour"),
    (r"\bonce a week\b|\bweekly\b|\bpar semaine\b|\bchaque semaine\b", "once a week", "1 fois par semaine"),
    (r"at bedtime|au coucher|before sleep", "at bedtime", "au coucher"),
    (r"in the morning|every morning|le matin|chaque matin", "in the morning", "le matin"),
    (r"in the evening|every evening|at night|le soir|chaque soir", "in the evening", "le soir"),
    (r"si (?:besoin|douleurs?|fièvre)|en cas de (?:douleurs?|fièvre)|as needed|if needed|when needed|\bprn\b|if (?:pain|fever)",
     "as needed", "si besoin"),
]
DURATION_RE = re.compile(
    r"(?:for|during|over|pendant|durant|sur)\s*(\d+)\s*(days?|weeks?|months?|jours?|semaines?|mois)\b", re.I
)
BARE_DURATION_RE = re.compile(r"(?<!every )(?<!toutes les )\b(\d+)\s*(days|jours|weeks|semaines)\b", re.I)
LONG_TERM_RE = re.compile(r"long[- ]term|au long cours|à vie|for life|chronic treatment|traitement de fond", re.I)
DURATION_UNITS = {
    "day": ("day", "jour"), "jour": ("day", "jour"),
    "week": ("week", "semaine"), "semaine": ("week", "semaine"),
    "month": ("month", "mois"), "mois": ("month", "mois"),
}
INSTRUCTIONS = [
    (r"after (?:meals?|eating|food)|après (?:les |le )?repas|après manger", "after meals", "après les repas"),
    (r"before (?:meals?|eating)|avant (?:les |le )?repas", "before meals", "avant les repas"),
    (r"with (?:food|meals?)|pendant (?:les |le )?repas|au cours (?:des|du) repas", "with meals", "pendant les repas"),
    (r"on an empty stomach|à jeun", "on an empty stomach", "à jeun"),
    (r"with (?:a glass of )?water|avec (?:un verre d')?eau", "with water", "avec de l'eau"),
    (r"do not drive|ne pas conduire", "do not drive", "ne pas conduire"),
    (r"avoid alcohol|pas d'alcool|éviter l'alcool", "avoid alcohol", "éviter l'alcool"),
]


# ---------------------------------------------------------------- diagnosis
def analyze_diagnosis(text: str, lang: str = "fr", kb: KnowledgeBase | None = None) -> dict:
    kb = kb or get_kb()
    corrected, corrections = kb.correct_text(text, lang)
    conditions, symptoms, meds = [], [], []
    seen = set()
    for m in kb.find(text, lang):
        key = (m.entity.kind, m.entity.en)
        if key in seen:
            continue
        seen.add(key)
        item = m.entity.to_dict(lang) | {"matched": m.matched_text, "confidence": round(m.score / 100, 2)}
        {"condition": conditions, "symptom": symptoms, "medication": meds}[m.entity.kind].append(item)
    return {
        "text": text,
        "corrected_text": corrected,
        "corrections": corrections,
        "conditions": conditions,
        "symptoms": symptoms,
        "medications_mentioned": meds,
        "engine": "rules",
    }


# ---------------------------------------------------------------- prescription
_CLAUSE_SPLIT = re.compile(r"[.;\n]+|\b(?:then|and then|next|puis|ensuite|et puis)\b", re.I)


def parse_prescription(text: str, lang: str = "fr", kb: KnowledgeBase | None = None) -> list[dict]:
    kb = kb or get_kb()
    items: list[dict] = []
    for clause in _CLAUSE_SPLIT.split(text):
        if not clause or not clause.strip():
            continue
        meds = kb.find(clause, lang, kinds={"medication"})
        if not meds:
            continue
        for i, m in enumerate(meds):
            seg_start = 0 if i == 0 else m.start
            seg_end = meds[i + 1].start if i + 1 < len(meds) else len(clause)
            segment = clause[seg_start:seg_end]
            # the drug name itself should not be parsed for doses ("vitamine B12")
            rest = segment.replace(m.matched_text, " ", 1)
            items.append(_parse_segment(rest, m.entity, lang))
    return items


def _parse_segment(segment: str, entity, lang: str) -> dict:
    idx = 1 if lang == "fr" else 0
    seg = _words_to_digits(segment)
    item = {
        "drug": entity.name(lang),
        "drug_class": entity.drug_class or "",
        "dosage": "",
        "form": "",
        "quantity": "",
        "frequency": "",
        "duration": "",
        "instructions": "",
    }
    if m := DOSE_RE.search(seg):
        unit = next(u for p, u in UNIT_MAP if re.fullmatch(p, m.group(2), re.I))
        item["dosage"] = f"{_fmt_num(m.group(1))} {unit}"

    if m := QTY_FORM_RE.search(seg):
        qty = m.group(1).replace("½", "0.5")
        form = next(v for p, v in FORMS.items() if re.fullmatch(p, m.group(2), re.I))
        item["form"] = form[idx]
        item["quantity"] = f"{_fmt_num(qty)} {form[idx]}"
    else:
        for p, v in STANDALONE_FORMS.items():
            if re.search(r"\b(?:" + p + r")\b", seg, re.I):
                item["form"] = v[idx]
                break

    freq_matched = None
    for pattern, en, fr in FREQUENCIES:
        if m := re.search(pattern, seg, re.I):
            n = next((g for g in m.groups() if g), "") if m.groups() else ""
            item["frequency"] = (fr if lang == "fr" else en).format(n=n)
            freq_matched = en
            break

    if m := DURATION_RE.search(seg) or BARE_DURATION_RE.search(seg):
        n, unit = m.group(1), m.group(2).lower()
        unit = unit if unit == "mois" else unit.rstrip("s")
        en_u, fr_u = DURATION_UNITS.get(unit, (unit, unit))
        if lang == "fr":
            item["duration"] = f"{n} {fr_u}{'' if fr_u == 'mois' or n == '1' else 's'}"
        else:
            item["duration"] = f"{n} {en_u}{'' if n == '1' else 's'}"
    elif LONG_TERM_RE.search(seg):
        item["duration"] = "au long cours" if lang == "fr" else "long-term"

    notes = []
    for pattern, en, fr in INSTRUCTIONS:
        if re.search(pattern, seg, re.I):
            notes.append(fr if lang == "fr" else en)
    if freq_matched != "as needed" and re.search(FREQUENCIES[-1][0], seg, re.I):
        notes.append("si besoin" if lang == "fr" else "as needed")
    item["instructions"] = ", ".join(notes)
    return item


# ---------------------------------------------------------------- safety
def check_safety(items: list[dict], allergies: str = "", lang: str = "fr", kb: KnowledgeBase | None = None) -> list[dict]:
    """Decision support only: flags allergies, duplicates and known interactions."""
    kb = kb or get_kb()
    warnings: list[dict] = []
    resolved = []
    for it in items:
        ent = kb.lookup_medication(it.get("drug", ""), lang)
        if ent:
            resolved.append((it.get("drug"), ent))

    # allergies
    allergy_norm = f" {normalize(allergies or '')} "
    if allergy_norm.strip():
        for drug, ent in resolved:
            hit = None
            names = [ent.en, ent.fr, *[a for v in ent.aliases.values() for a in v]]
            if any(normalize(n) and f" {normalize(n)} " in allergy_norm for n in names):
                hit = drug
            for tag in ent.tags:
                for word in kb.allergy_groups.get(tag, []):
                    if f" {normalize(word)} " in allergy_norm:
                        hit = f"{drug} ({ent.drug_class})"
            if hit:
                warnings.append({
                    "severity": "high", "type": "allergy", "drugs": [drug],
                    "message": (f"Allergie déclarée du patient : « {allergies} ». Vérifier {hit}."
                                if lang == "fr" else f"Patient allergy on file: “{allergies}”. Check {hit}."),
                })

    # duplicates of the same therapeutic class
    by_class: dict[str, list[str]] = {}
    for drug, ent in resolved:
        if ent.drug_class:
            by_class.setdefault(ent.drug_class, []).append(drug)
    for cls, drugs in by_class.items():
        if len(drugs) > 1:
            warnings.append({
                "severity": "medium", "type": "duplicate", "drugs": drugs,
                "message": (f"Doublon de classe thérapeutique ({cls}) : {', '.join(drugs)}."
                            if lang == "fr" else f"Same therapeutic class ({cls}) prescribed twice: {', '.join(drugs)}."),
            })

    # pairwise interactions
    seen = set()
    for i in range(len(resolved)):
        for j in range(i + 1, len(resolved)):
            (d1, e1), (d2, e2) = resolved[i], resolved[j]
            for rule in kb.interactions:
                a, b = rule["a"], rule["b"]
                if (a in e1.tags and b in e2.tags) or (b in e1.tags and a in e2.tags):
                    key = (d1, d2, rule["en"])
                    if key not in seen:
                        seen.add(key)
                        warnings.append({"severity": rule["severity"], "type": "interaction", "drugs": [d1, d2],
                                         "message": rule["fr" if lang == "fr" else "en"]})
    order = {"high": 0, "medium": 1, "low": 2}
    warnings.sort(key=lambda w: order.get(w["severity"], 3))
    return warnings


# ---------------------------------------------------------------- full dictation
SECTION_KEYWORDS = {
    "prescription": r"\b(?:prescription|ordonnance|je prescris|je prescrit|traitement|treatment|i prescribe|prescribe|rx)\b",
    "certificate": r"\b(?:certificat|certificate|arrêt de travail|arret de travail|sick leave|sick note|work leave)\b",
    "diagnosis": r"\b(?:diagnostic|diagnosis|diagnosed|le patient présente|patient presents|motif|examen)\b",
}


def split_consultation(text: str) -> dict:
    """Split a single dictation into sections at the first spoken keyword of each."""
    marks = []
    for section, pattern in SECTION_KEYWORDS.items():
        if m := re.search(pattern, text, re.I):
            marks.append((m.start(), section))
    marks.sort()
    sections = {"diagnosis": "", "prescription": "", "certificate": ""}
    if not marks:
        sections["diagnosis"] = text.strip()
        return sections
    if marks[0][0] > 0:
        sections["diagnosis"] += text[: marks[0][0]].strip()
    for i, (pos, section) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        chunk = text[pos:end].strip(" ,.;:\n")
        sections[section] = (sections[section] + " " + chunk).strip()
    return sections


CERT_TYPES = [
    ("sick_leave", r"arrêt de travail|arret de travail|sick leave|sick note|repos|rest from work|off work"),
    ("fitness", r"aptitude|non contre-indication|non-contre-indication|fit for|fitness|sport"),
    ("attendance", r"présence|attendance|attest"),
]


def parse_certificate(text: str, lang: str = "fr") -> dict | None:
    if not text.strip():
        return None
    seg = _words_to_digits(text)
    cert = {"type": "custom", "days": 0, "start_date": date.today().isoformat(), "sport": "", "remarks": text.strip()}
    for ctype, pattern in CERT_TYPES:
        if re.search(pattern, seg, re.I):
            cert["type"] = ctype
            break
    if m := re.search(r"(\d+)\s*(?:days?|jours?)", seg, re.I):
        cert["days"] = int(m.group(1))
    if cert["type"] == "fitness":
        if m := re.search(r"(?:pratique (?:du|de la|de l')|practice of|sport\s*:?)\s*([\w\s-]{3,30})", seg, re.I):
            cert["sport"] = m.group(1).strip()
    if cert["type"] != "custom":
        cert["remarks"] = ""  # the dictation is already expressed by type/days
    return cert


def structure_rules(text: str, lang: str = "fr") -> dict:
    parts = split_consultation(text)
    diag = analyze_diagnosis(parts["diagnosis"], lang) if parts["diagnosis"] else None
    return {
        "engine": "rules",
        "sections": parts,
        "diagnosis": {
            "summary": diag["corrected_text"] if diag else "",
            "conditions": diag["conditions"] if diag else [],
            "symptoms": diag["symptoms"] if diag else [],
            "notes": "",
        },
        "prescription": parse_prescription(parts["prescription"] or text, lang),
        "certificate": parse_certificate(parts["certificate"], lang),
    }
