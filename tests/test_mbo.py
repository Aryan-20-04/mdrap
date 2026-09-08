import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mbo import OrderBookMBO, OrderSide, RestingOrder, QueuePositionInfo


def test_mbo_order_add_and_stats():
    book = OrderBookMBO(instrument_id="AAPL")

    assert book.order_add("o1", "BUY", 150.00, 100.0, venue="NASDAQ")
    assert book.order_add("o2", "BUY", 149.95, 200.0, venue="ARCA")
    assert book.order_add("o3", "SELL", 150.05, 150.0, venue="BATS")
    assert book.order_add("o4", "SELL", 150.10, 300.0, venue="IEX")

    # Duplicate order_id rejected
    assert not book.order_add("o1", "BUY", 150.00, 50.0)
    # Non-positive price or size rejected
    assert not book.order_add("o5", "BUY", -10.0, 100.0)
    assert not book.order_add("o6", "SELL", 150.0, 0.0)

    st = book.stats()
    assert st["active_orders"] == 4
    assert st["bid_levels"] == 2
    assert st["ask_levels"] == 2
    assert st["total_adds"] == 4


def test_mbo_queue_priority_and_rank():
    book = OrderBookMBO(instrument_id="NVDA")

    # 3 orders arriving at the same price rung: 130.00 BUY
    book.order_add("ord_A", "BUY", 130.00, 10.0, venue="NASDAQ")
    book.order_add("ord_B", "BUY", 130.00, 25.0, venue="ARCA")
    book.order_add("ord_C", "BUY", 130.00, 50.0, venue="BATS")

    pos_a = book.get_queue_position("ord_A")
    assert pos_a is not None
    assert pos_a.queue_rank == 1
    assert pos_a.orders_ahead == 0
    assert pos_a.size_ahead == 0.0
    assert pos_a.total_level_size == 85.0
    assert pos_a.total_level_orders == 3
    assert pos_a.fill_probability_pct == 100.0

    pos_b = book.get_queue_position("ord_B")
    assert pos_b is not None
    assert pos_b.queue_rank == 2
    assert pos_b.orders_ahead == 1
    assert pos_b.size_ahead == 10.0
    assert pos_b.total_level_size == 85.0

    pos_c = book.get_queue_position("ord_C")
    assert pos_c is not None
    assert pos_c.queue_rank == 3
    assert pos_c.orders_ahead == 2
    assert pos_c.size_ahead == 35.0
    assert pos_c.total_level_size == 85.0

    # Non-existent order
    assert book.get_queue_position("non_existent") is None


def test_mbo_order_modify_preserves_priority_on_size_reduction():
    book = OrderBookMBO(instrument_id="MSFT")

    book.order_add("m1", "BUY", 420.00, 100.0, venue="NASDAQ")
    book.order_add("m2", "BUY", 420.00, 200.0, venue="ARCA")
    book.order_add("m3", "BUY", 420.00, 300.0, venue="BATS")

    # Reduce m2 from 200 to 80 (partial cancel)
    assert book.order_modify("m2", new_size=80.0)

    # m2 must PRESERVE rank 2 (behind m1, in front of m3)
    pos_m1 = book.get_queue_position("m1")
    assert pos_m1.queue_rank == 1
    assert pos_m1.size_ahead == 0.0

    pos_m2 = book.get_queue_position("m2")
    assert pos_m2.queue_rank == 2
    assert pos_m2.size == 80.0
    assert pos_m2.orders_ahead == 1
    assert pos_m2.size_ahead == 100.0  # size of m1

    pos_m3 = book.get_queue_position("m3")
    assert pos_m3.queue_rank == 3
    assert pos_m3.orders_ahead == 2
    assert pos_m3.size_ahead == 180.0  # 100 (m1) + 80 (m2)

    # Level total size updated to 100 + 80 + 300 = 480
    assert pos_m3.total_level_size == 480.0
    assert book.bids[420.00].venue_sizes["ARCA"] == 80.0


def test_mbo_order_modify_loses_priority_on_size_increase():
    book = OrderBookMBO(instrument_id="TSLA")

    book.order_add("t1", "BUY", 250.00, 50.0)
    book.order_add("t2", "BUY", 250.00, 100.0)
    book.order_add("t3", "BUY", 250.00, 150.0)

    # Increase t1 size: 50 -> 80. Must lose priority and move to tail!
    assert book.order_modify("t1", new_size=80.0)

    # New order in queue should be: t2 (rank 1), t3 (rank 2), t1 (rank 3)
    pos_t2 = book.get_queue_position("t2")
    assert pos_t2.queue_rank == 1
    assert pos_t2.size_ahead == 0.0

    pos_t3 = book.get_queue_position("t3")
    assert pos_t3.queue_rank == 2
    assert pos_t3.size_ahead == 100.0

    pos_t1 = book.get_queue_position("t1")
    assert pos_t1.queue_rank == 3
    assert pos_t1.size == 80.0
    assert pos_t1.orders_ahead == 2
    assert pos_t1.size_ahead == 250.0  # 100 + 150


def test_mbo_order_modify_price_change():
    book = OrderBookMBO(instrument_id="AMZN")

    book.order_add("a1", "SELL", 185.00, 100.0, venue="NASDAQ")
    book.order_add("a2", "SELL", 185.00, 150.0, venue="ARCA")

    # Move a1 to higher price rung: 185.50
    assert book.order_modify("a1", new_size=100.0, new_price=185.50)

    # Old level 185.00 should now only contain a2
    assert 185.00 in book.asks
    assert book.asks[185.00].order_count == 1
    pos_a2 = book.get_queue_position("a2")
    assert pos_a2.queue_rank == 1
    assert pos_a2.orders_ahead == 0

    # New level 185.50 should contain a1
    assert 185.50 in book.asks
    pos_a1 = book.get_queue_position("a1")
    assert pos_a1.queue_rank == 1
    assert pos_a1.price == 185.50


def test_mbo_order_execute():
    book = OrderBookMBO(instrument_id="META")

    book.order_add("m1", "BUY", 500.00, 100.0, venue="NASDAQ")
    book.order_add("m2", "BUY", 500.00, 200.0, venue="BATS")

    # Partial execution of m1 (40 lots)
    ok, remaining = book.order_execute("m1", filled_size=40.0)
    assert ok
    assert remaining == 60.0
    pos_m1 = book.get_queue_position("m1")
    assert pos_m1.size == 60.0
    assert pos_m1.queue_rank == 1  # priority preserved

    # Complete execution of remaining m1 (60 lots)
    ok, remaining = book.order_execute("m1", filled_size=60.0)
    assert ok
    assert remaining == 0.0
    assert "m1" not in book.orders

    # m2 is now rank 1
    pos_m2 = book.get_queue_position("m2")
    assert pos_m2.queue_rank == 1
    assert pos_m2.orders_ahead == 0


def test_mbo_order_cancel():
    book = OrderBookMBO(instrument_id="GOOGL")

    book.order_add("g1", "SELL", 170.00, 100.0)
    assert book.order_cancel("g1")
    assert not book.order_cancel("g1")  # second cancel fails

    # Level 170.00 should be removed from asks
    assert 170.00 not in book.asks
    assert book.stats()["active_orders"] == 0


def test_mbo_project_l2_and_micro_price():
    book = OrderBookMBO(instrument_id="AAPL", max_depth_levels=5)

    # 2 bid levels
    book.order_add("b1", "BUY", 150.00, 200.0, venue="NASDAQ")
    book.order_add("b2", "BUY", 150.00, 100.0, venue="ARCA")
    book.order_add("b3", "BUY", 149.90, 500.0, venue="BATS")

    # 2 ask levels
    book.order_add("a1", "SELL", 150.10, 100.0, venue="IEX")
    book.order_add("a2", "SELL", 150.20, 400.0, venue="NASDAQ")

    l2 = book.project_l2()
    assert l2["instrument"] == "AAPL"
    assert l2["best_bid"] == 150.00
    assert l2["best_ask"] == 150.10
    assert l2["spread"] == 0.10
    assert not l2["is_crossed"]

    # Top bid size = 200 + 100 = 300; top ask size = 100
    assert l2["bids"][0]["size"] == 300.0
    assert l2["bids"][0]["order_count"] == 2
    assert l2["bids"][0]["venues"] == {"NASDAQ": 200.0, "ARCA": 100.0}
    assert l2["asks"][0]["size"] == 100.0

    # Micro-price: (150.10 * 300 + 150.00 * 100) / (300 + 100) = (45030 + 15000) / 400 = 150.075
    assert l2["micro_price"] == 150.075
    # Imbalance: (300 - 100) / 400 = +0.50 (heavy bid pressure)
    assert l2["imbalance_ratio"] == 0.50
