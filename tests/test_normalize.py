from scanner import normalize


def test_rocket_gang_becomes_team_rocket():
    q = normalize.ebay_query("1997 Pokemon Japanese Rocket Gang Dark Hypno #97 CGC 10 Gem Mint")
    assert "Team Rocket" in q
    assert "Rocket Gang" not in q
    assert "CGC" not in q


def test_nintedo_typo_fixed():
    q = normalize.ebay_query("2000 Pokemon Movie Promo Nintedo Error Ancient Mew CGC 10")
    assert "Nintendo" in q


def test_zero_padding_normalized():
    q = normalize.ebay_query("1997 Bandai Carddass Prism Charizard #006 CGC 10 Gem Mint")
    assert "#6" in q and "#006" not in q


def test_card_key_dedupes_padding_and_year():
    a = normalize.card_key("1999 Pokemon Jungle Snorlax #11 CGC 10 Gem Mint")
    b = normalize.card_key("2000 Pokemon Jungle Snorlax #011 CGC 10 GEM MINT")
    assert a == b


def test_query_variants_ordering():
    v = normalize.query_variants("1999 Pokemon Jungle Snorlax #11 CGC 10 Gem Mint")
    assert v[0].startswith("1999 Pokemon Jungle Snorlax #11")
    assert any("#" not in q for q in v[1:])


def test_comp_filter_rejects_wrong_card_number():
    sales = [
        {"title": "1999 Pokemon Fossil 1st Edition Dragonite 19/62 CGC 10"},
        {"title": "1999 Pokemon Fossil 1st Edition Holo Dragonite #4 CGC 10"},
        {"title": "1999 Fossil 1st Edition Dragonite #19 CGC 10 Gem Mint"},
    ]
    kept = normalize.comp_filter(
        "1999 Pokemon Fossil 1st Edition Dragonite #19 CGC 10 GEM MINT", sales)
    assert len(kept) == 2
    assert all("#4" not in s["title"] for s in kept)


def test_comp_filter_year_does_not_false_match_number():
    # card #19 must not match the '19' inside '1999'
    sales = [{"title": "1999 Pokemon Fossil Aerodactyl CGC 10"}]
    kept = normalize.comp_filter("1999 Pokemon Fossil Dragonite #19 CGC 10", sales)
    assert kept == []


def test_comp_filter_edition_and_language_agreement():
    sales = [
        {"title": "1999 Pokemon Base Set Pikachu #58 CGC 10"},
        {"title": "1999 Pokemon Base Set 1st Edition Pikachu #58 CGC 10"},
        {"title": "1999 Pokemon Japanese Base Set Pikachu #58 CGC 10"},
    ]
    kept = normalize.comp_filter("1999 Pokemon Base Set Pikachu #58 CGC 10 GEM MINT", sales)
    assert [s["title"] for s in kept] == ["1999 Pokemon Base Set Pikachu #58 CGC 10"]
    kept_1st = normalize.comp_filter(
        "1999 Pokemon Base Set 1st Edition Pikachu #58 CGC 10", sales)
    assert [s["title"] for s in kept_1st] == ["1999 Pokemon Base Set 1st Edition Pikachu #58 CGC 10"]


def test_comp_filter_error_variant_agreement():
    sales = [
        {"title": "2000 Pokemon Movie Promo Ancient Mew CGC 10"},
        {"title": "2000 Pokemon Movie Promo Nintendo Error Ancient Mew CGC 10"},
    ]
    plain = normalize.comp_filter("2000 Pokemon Movie Promo Ancient Mew CGC 10 GEM MINT", sales)
    assert len(plain) == 1 and "Error" not in plain[0]["title"]
    err = normalize.comp_filter("2000 Pokemon Movie Promo Nintedo Error Ancient Mew CGC 10", sales)
    assert len(err) == 1 and "Error" in err[0]["title"]


def test_comp_filter_number_ten_not_satisfied_by_grade():
    # C1: card #10 must NOT match the "10" in "CGC 10" on a different card
    sales = [
        {"title": "2002 Pokemon Neo Destiny Dark Typhlosion #10 CGC 10 Gem Mint"},
        {"title": "2000 Pokemon Neo Genesis Typhlosion #17 CGC 10 Gem Mint"},
    ]
    kept = normalize.comp_filter(
        "2002 Pokemon Neo Destiny Dark Typhlosion #10 CGC 10 Gem Mint", sales)
    assert [s["title"] for s in kept] == [sales[0]["title"]]


def test_comp_filter_subject_word_boundary():
    # C1: 'mew' (Ancient Mew) must not match 'Mewtwo'
    sales = [
        {"title": "2000 Pokemon Movie Promo Ancient Mew CGC 10 Gem Mint"},
        {"title": "1999 Pokemon Movie Promo Mewtwo #3 CGC 10 Gem Mint"},
    ]
    kept = normalize.comp_filter("2000 Pokemon Movie Promo Ancient Mew CGC 10 GEM MINT", sales)
    assert all("mewtwo" not in s["title"].lower() for s in kept)
    assert len(kept) == 1


def test_comp_filter_accepts_zero_padded_number():
    # H3: FC "#6" must accept eBay "#006" (and vice versa)
    sales = [{"title": "1997 Pokemon Bandai Carddass Charizard #006 CGC 10 Gem Mint"}]
    kept = normalize.comp_filter(
        "1997 Pokemon Bandai Carddass Charizard #6 CGC 10 Gem Mint", sales)
    assert len(kept) == 1


def test_comp_filter_japanese_without_the_word():
    # H3: JP set-name hint on the eBay side counts even without literal "Japanese"
    sales = [{"title": "2000 Pokemon CoroCoro Comic Promo Hama-Chan's Slowking CGC 10 Gem Mint"}]
    kept = normalize.comp_filter(
        "2000 Pokemon Japanese CoroCoro Slowking CGC 10 Gem Mint", sales)
    assert len(kept) == 1
