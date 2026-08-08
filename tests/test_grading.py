from scanner import grading


def test_pristine_vs_gem_never_pooled():
    assert grading.classify_grade("1998 Snorlax #143 CGC 10 Pristine") == grading.CGC10_PRISTINE
    assert grading.classify_grade("1998 Snorlax #143 CGC 10 Gem Mint") == grading.CGC10_GEM
    assert grading.classify_grade("1998 Snorlax #143 CGC 10") == grading.CGC10_UNSPEC


def test_real_fc_title_forms():
    assert grading.classify_grade(
        "2007 Pokemon Japanese Secret Of The Lakes Exeggcute CGC 10 GEM MINT") == grading.CGC10_GEM
    assert grading.classify_grade(
        "1998 Pokemon Japanese Best Scene Anime GB Pocket Nurse Joy #33 CGC 10 Pristine") == grading.CGC10_PRISTINE
    assert grading.classify_grade("Charizard PSA 10") == grading.PSA10
    assert grading.classify_grade("Charizard CGC 9.5") == grading.OTHER


def test_language_detection_without_the_word_japanese():
    # "Japanese" is sometimes omitted — set names must imply language
    assert grading.detect_language("2000 Pokemon CoroCoro Erika's Dratini #147") == "JA"
    assert grading.detect_language("1997 Pokemon Rocket Gang Dark Hypno #97") == "JA"
    assert grading.detect_language("1999 Pokemon Base Set Charizard #4") == "EN"


def test_bandai_flagged_as_non_tcg():
    assert grading.detect_product_line("1997 Pokemon Bandai Carddass Prism Charizard #006") == "BANDAI"
    assert grading.detect_product_line("2000 Topps Pokemon Series 2 Clear Card Pikachu") == "TOPPS"
    assert grading.detect_product_line("1999 Pokemon Base Set Charizard") == "TCG"


def test_year_extraction():
    assert grading.extract_year("1999 Pokemon Base Set Charizard") == 1999
    assert grading.extract_year("Pokemon Promo Mew") is None
