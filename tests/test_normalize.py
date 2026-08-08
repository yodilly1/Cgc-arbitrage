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
