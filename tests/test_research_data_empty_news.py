from gld_scalper.research_data import news_response_to_records


class EmptyAlpacaNewsSet:
    data = None

    @property
    def df(self):
        raise KeyError("None of ['id'] are in the columns")


def test_empty_alpaca_news_page_is_a_valid_zero_row_result() -> None:
    assert news_response_to_records(EmptyAlpacaNewsSet()) == []
