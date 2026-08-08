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
