"""
Unit tests for FIX 4.2/4.4 Protocol Engine (Gap 10).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from fix_engine import FIXTag, FIXMessage, FIXSession, SOH


def test_fix_message_encode_and_parse():
    msg = FIXMessage('D')
    msg.set(FIXTag.CL_ORD_ID, 'ORD123')
    msg.set(FIXTag.SYMBOL, 'AAPL')
    msg.set(FIXTag.SIDE, '1')
    msg.set(FIXTag.ORDER_QTY, 100)
    msg.set(FIXTag.ORD_TYPE, '2')
    msg.set(FIXTag.PRICE, 150.25)

    encoded = msg.encode(sender='CLIENT', target='EXCHANGE', seq_num=1)
    assert f"{FIXTag.BEGIN_STRING}=FIX.4.2" in encoded
    assert f"{FIXTag.MSG_TYPE}=D" in encoded
    assert f"{FIXTag.CL_ORD_ID}=ORD123" in encoded
    assert f"{FIXTag.SYMBOL}=AAPL" in encoded
    assert f"{FIXTag.CHECK_SUM}=" in encoded
    assert encoded.endswith(SOH)

    # Parse back
    parsed = FIXMessage.parse(encoded)
    assert parsed.get(FIXTag.MSG_TYPE) == 'D'
    assert parsed.get(FIXTag.CL_ORD_ID) == 'ORD123'
    assert parsed.get(FIXTag.SYMBOL) == 'AAPL'
    assert parsed.get_float(FIXTag.PRICE) == 150.25
    assert parsed.get_int(FIXTag.ORDER_QTY) == 100


def test_fix_message_pipe_delimited_parsing():
    raw_pipe = "8=FIX.4.2|9=65|35=0|49=SERVER|56=CLIENT|34=2|52=20260315-12:00:00.000|10=128|"
    parsed = FIXMessage.parse(raw_pipe)
    assert parsed.get(FIXTag.BEGIN_STRING) == 'FIX.4.2'
    assert parsed.get(FIXTag.MSG_TYPE) == '0'
    assert parsed.get(FIXTag.SENDER_COMP_ID) == 'SERVER'
    assert parsed.get_int(FIXTag.MSG_SEQ_NUM) == 2


def test_fix_session_logon_flow():
    session = FIXSession(sender_comp_id='MDRAP', target_comp_id='BROKER')
    assert session.is_logged_on is False

    logon_msg = session.create_logon(heartbeat_int=30)
    assert logon_msg.get(FIXTag.MSG_TYPE) == 'A'
    assert logon_msg.get_int(108) == 30

    # Simulate broker logon reply
    broker_logon_raw = (
        f"8=FIX.4.2{SOH}9=65{SOH}35=A{SOH}49=BROKER{SOH}56=MDRAP{SOH}"
        f"34=1{SOH}52=20260315-12:00:00.000{SOH}108=30{SOH}10=050{SOH}"
    )
    incoming = session.process_incoming(broker_logon_raw)
    assert incoming.get(FIXTag.MSG_TYPE) == 'A'
    assert session.is_logged_on is True
    assert session.in_seq == 1


def test_logon_rejected_on_unknown_compid():
    session = FIXSession(sender_comp_id='MDRAP', target_comp_id='BROKER')
    # Incoming logon with wrong SenderCompID (ROGUE instead of BROKER)
    rogue_logon_raw = (
        f"8=FIX.4.2{SOH}9=65{SOH}35=A{SOH}49=ROGUE{SOH}56=MDRAP{SOH}"
        f"34=1{SOH}52=20260315-12:00:00.000{SOH}108=30{SOH}10=050{SOH}"
    )
    incoming = session.process_incoming(rogue_logon_raw)
    assert incoming.get(FIXTag.MSG_TYPE) == 'A'
    assert session.is_logged_on is False



def test_fix_session_order_creation():
    session = FIXSession(sender_comp_id='MDRAP', target_comp_id='BROKER')
    order_msg = session.create_new_order_single(
        cl_ord_id='CL001',
        symbol='MSFT',
        side='1',
        qty=50.0,
        ord_type='2',
        price=420.50
    )
    assert order_msg.get(FIXTag.MSG_TYPE) == 'D'
    assert order_msg.get(FIXTag.CL_ORD_ID) == 'CL001'
    assert order_msg.get(FIXTag.SYMBOL) == 'MSFT'
    assert order_msg.get_float(FIXTag.PRICE) == 420.50


def test_fix_session_order_cancel():
    session = FIXSession(sender_comp_id='MDRAP', target_comp_id='BROKER')
    cancel_msg = session.create_order_cancel(
        cl_ord_id='CX001',
        orig_cl_ord_id='CL001',
        symbol='MSFT',
        side='1'
    )
    assert cancel_msg.get(FIXTag.MSG_TYPE) == 'F'
    assert cancel_msg.get(FIXTag.CL_ORD_ID) == 'CX001'
    assert cancel_msg.get(41) == 'CL001'
