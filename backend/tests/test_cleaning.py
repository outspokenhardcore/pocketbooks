"""Text cleaning, incl. repair of broken-font ligatures."""
from app.pdfproc.cleaning import clean_text


def test_broken_ipa_ligatures_repaired():
    # PDF font subset maps f-ligature glyphs to IPA codepoints.
    assert clean_text("ɹsh") == "fish"
    assert clean_text("Lucasɹlm") == "Lucasfilm"
    assert clean_text("reɻect") == "reflect"
    assert clean_text("oʃ") == "off"
    assert clean_text("diɽcult") == "difficult"
    assert clean_text("muʀed") == "muffled"


def test_proper_unicode_ligatures_still_handled():
    assert clean_text("ﬁre") == "fire"
    assert clean_text("ﬂow") == "flow"


def test_smart_quotes_normalized():
    assert clean_text("Je’daii") == "Je'daii"
    assert clean_text("“Hello”") == '"Hello"'


def test_trademark_marks_stripped():
    assert clean_text("Time Machine® universe") == "Time Machine universe"
    assert clean_text("HyperBolic™") == "HyperBolic"


def test_cyrillic_homoglyphs_folded():
    assert clean_text("Corusсant") == "Coruscant"   # с U+0441 -> c
    assert clean_text("раrtу") == "party"            # р,а,у Cyrillic
    assert clean_text("Наrру") == "Harpy"            # Н,а,р,у Cyrillic


def test_e_acute_substitutes_repaired_but_copyright_kept():
    assert clean_text("caf©") == "café"
    assert clean_text("Padm¨") == "Padmé"
    assert clean_text("prot©g©") == "protégé"
    # standalone copyright sign must survive (not adjacent to a letter)
    assert clean_text("© 2014 Lucasfilm") == "© 2014 Lucasfilm"
