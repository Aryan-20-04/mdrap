import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from corporate_actions import CorporateActionsEngine, ActionType, load_well_known


@pytest.fixture
def engine():
    return CorporateActionsEngine(db_path=":memory:")


def test_forward_split_adjustment(engine):
    engine.add_split("AAPL", "2020-08-31", 4.0, description="4 for 1 split")
    adj_price = engine.adjust_price("AAPL", 500.0, "2020-08-30", adjustment="split")
    assert adj_price == 125.0


def test_reverse_split(engine):
    engine.add_split("XYZ", "2021-01-01", 0.1, description="1 for 10 reverse split")
    adj_price = engine.adjust_price("XYZ", 5.0, "2020-12-31", adjustment="split")
    # 5.0 / 0.1 = 50.0
    assert adj_price == 50.0


def test_volume_adjustment(engine):
    engine.add_split("AAPL", "2020-08-31", 4.0, description="4 for 1 split")
    adj_vol = engine.adjust_volume("AAPL", 1000.0, "2020-08-30")
    # 1000.0 * 4.0 = 4000.0
    assert adj_vol == 4000.0


def test_dividend_adjustment(engine):
    engine.add_dividend("DIV", "2022-05-15", 2.0, description="Quarterly dividend")
    # For a total return or dividend adjustment, price would be adjusted downwards historically
    # Exact math depends on implementation, but let's test the return value is not None
    adj_price = engine.adjust_price("DIV", 100.0, "2022-05-14", adjustment="dividend")
    assert adj_price is not None
    assert adj_price != 100.0


def test_ticker_change_resolution(engine):
    engine.add_ticker_change("FB", "META", "2022-06-09", description="Name change")
    assert engine.resolve_symbol("FB") == "META"
    assert engine.resolve_symbol("META") == "META"


def test_delisting_detection(engine):
    engine.add_delisting("TWTR", "2022-10-28", description="Acquired")
    assert engine.is_active("TWTR") is False
    assert engine.is_active("AAPL") is True


def test_multiple_splits_cumulative(engine):
    engine.add_split("SPLIT", "2010-01-01", 2.0)
    engine.add_split("SPLIT", "2015-01-01", 3.0)
    factor = engine.adjustment_factor("SPLIT", "2009-12-31")
    assert factor.cumulative_split_factor == 6.0


def test_actions_for_symbol(engine):
    engine.add_split("AAPL", "2014-06-09", 7.0)
    engine.add_split("AAPL", "2020-08-31", 4.0)
    actions = engine.actions_for("AAPL")
    assert len(actions) == 2
    # Ensure they are sorted by date
    assert actions[0].effective_date == "2014-06-09"
    assert actions[1].effective_date == "2020-08-31"


def test_load_well_known(engine):
    load_well_known(engine)
    # Check AAPL split
    aapl_actions = engine.actions_for("AAPL")
    assert len(aapl_actions) > 0

    # Check FB -> META
    assert engine.resolve_symbol("FB") == "META"

    # Check TWTR delisted
    assert engine.is_active("TWTR") is False


def test_symbol_history(engine):
    engine.add_ticker_change("FB", "META", "2022-06-09")
    history = engine.symbol_history("META")
    assert isinstance(history, list)
    assert "FB" in history
    assert "META" in history


def test_summary(engine):
    engine.add_split("AAPL", "2020-08-31", 4.0)
    engine.add_ticker_change("FB", "META", "2022-06-09")
    engine.add_delisting("TWTR", "2022-10-28")
    summary_data = engine.summary()
    assert isinstance(summary_data, dict)
    assert len(summary_data) > 0


def test_adjustment_factor(engine):
    engine.add_split("AAPL", "2020-08-31", 4.0)
    factor = engine.adjustment_factor("AAPL", "2020-08-30")
    assert factor.symbol == "AAPL"
    assert factor.cumulative_split_factor == 4.0
    assert hasattr(factor, "combined_factor")
