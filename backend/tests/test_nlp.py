from app import nlp
from app.medical_terms import get_kb


def test_fuzzy_matches_misspelled_drug():
    matches = get_kb().find("amoxiciline 1 g", "fr", kinds={"medication"})
    assert matches and matches[0].entity.en == "Amoxicillin"


def test_short_words_are_not_fuzzy_matched():
    assert not get_kb().find("le moral est bon", "fr", kinds={"medication"})


def test_correct_text_fixes_asr_errors():
    text, corrections = get_kb().correct_text("prendre de l'ibuprofene et du paracetamole", "fr")
    assert "Paracétamol" in text
    assert corrections


def test_analyze_diagnosis_fr():
    r = nlp.analyze_diagnosis("Le patient présente une fièvre et une toux sèche, angine aiguë", "fr")
    assert any(c["code"] == "J03.9" for c in r["conditions"])
    names = {s["en"] for s in r["symptoms"]}
    assert {"Fever", "Dry cough"} <= names


def test_analyze_diagnosis_en():
    r = nlp.analyze_diagnosis("patient with high blood pressure and type 2 diabetes, complains of headache", "en")
    codes = {c["code"] for c in r["conditions"]}
    assert {"I10", "E11.9"} <= codes


def test_parse_prescription_fr_without_punctuation():
    items = nlp.parse_prescription(
        "amoxicilline 1 g trois fois par jour pendant sept jours paracétamol 500 mg un comprimé si besoin", "fr")
    assert len(items) == 2
    amox, para = items
    assert amox["drug"] == "Amoxicilline"
    assert amox["dosage"] == "1 g"
    assert amox["frequency"] == "3 fois par jour"
    assert amox["duration"] == "7 jours"
    assert para["dosage"] == "500 mg"
    assert para["quantity"] == "1 comprimé"
    assert para["frequency"] == "si besoin"


def test_parse_prescription_en():
    items = nlp.parse_prescription(
        "Ibuprofen 400 mg twice a day after meals for 5 days. Omeprazole 20 mg once daily for one month", "en")
    ibu, ome = items
    assert ibu["frequency"] == "2 times a day"
    assert ibu["instructions"] == "after meals"
    assert ibu["duration"] == "5 days"
    assert ome["duration"] == "1 month"


def test_safety_allergy_and_interaction():
    items = [{"drug": "Amoxicilline"}, {"drug": "Warfarine"}, {"drug": "Ibuprofène"}]
    warnings = nlp.check_safety(items, "allergie à la pénicilline", "fr")
    types = {w["type"] for w in warnings}
    assert "allergy" in types and "interaction" in types
    assert warnings[0]["severity"] == "high"


def test_structure_rules_splits_sections():
    r = nlp.structure_rules(
        "Diagnostic angine aiguë avec fièvre. Ordonnance amoxicilline 1 g deux fois par jour pendant 6 jours. "
        "Certificat arrêt de travail de 3 jours", "fr")
    assert r["diagnosis"]["conditions"][0]["code"] == "J03.9"
    assert r["prescription"][0]["frequency"] == "2 fois par jour"
    assert r["certificate"]["type"] == "sick_leave" and r["certificate"]["days"] == 3


def test_si_douleur_is_as_needed():
    item = nlp.parse_prescription("paracétamol 1 g si douleur", "fr")[0]
    assert item["frequency"] == "si besoin"
