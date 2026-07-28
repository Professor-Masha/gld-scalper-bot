from gld_scalper.shortability import check_asset_shortability


class Asset:
    symbol = "GLD"
    status = "active"
    tradable = True
    marginable = True
    shortable = False


def test_short_blocked_when_asset_not_shortable():
    result = check_asset_shortability(Asset(), buying_power=1_000_000, required_notional=5_000)
    assert not result.allowed
    assert result.reason == "GLD is not shortable"
