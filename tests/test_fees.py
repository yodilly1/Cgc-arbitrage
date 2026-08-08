from scanner import fees


def test_net_proceeds_anchor():
    # Verified identity from the brief: selling at $1,000 nets exactly $855.10
    assert round(fees.ebay_net_proceeds(1000), 2) == 855.10


def test_net_proceeds_above_threshold():
    # $10,000: 13.25% on first $7,500 + 2.35% on the rest + $0.40 + $12 ship
    expected = 10000 - (7500 * 0.1325 + 2500 * 0.0235) - 0.40 - 12.00
    assert round(fees.ebay_net_proceeds(10000), 2) == round(expected, 2)


def test_breakeven_is_about_1_15x():
    be = fees.breakeven_ebay_price(1000)
    assert 1.14 < be / 1000 < 1.18
    assert abs(fees.ebay_net_proceeds(be) - 1000) < 0.01


def test_hammer_round_trip():
    assert fees.hammer_from_total(1200) == 1000
    assert fees.total_from_hammer(1000) == 1200


def test_max_fc_total():
    # paying max_fc_total then selling at target hits exactly the margin
    target = 1000.0
    max_total = fees.max_fc_total(target, margin=0.25)
    net = fees.ebay_net_proceeds(target)
    assert abs(net / max_total - 1.25) < 1e-9


def test_store_fee_schedule():
    # Basic Store: 12.35% to $2,500 then 2.35%
    expected = 3000 - (2500 * 0.1235 + 500 * 0.0235) - 0.40 - 12.00
    assert round(fees.ebay_net_proceeds(3000, store=True), 2) == round(expected, 2)
