"""
FIX Protocol Engine for MDRAP.

This module implements a lightweight FIX 4.2/4.4 session protocol and
order routing engine for institutional execution connectivity.
It provides message parsing, serialization, and session state management.
"""

from typing import Any
from datetime import datetime, timezone

try:
    import fastpath
except ImportError:
    fastpath = None

__stability__ = "experimental"

SOH = "\x01"


class FIXTag:
    BEGIN_STRING = 8
    BODY_LENGTH = 9
    MSG_TYPE = 35
    SENDER_COMP_ID = 49
    TARGET_COMP_ID = 56
    MSG_SEQ_NUM = 34
    SENDING_TIME = 52
    CHECK_SUM = 10

    # Application tags
    CL_ORD_ID = 11
    ORDER_ID = 37
    SYMBOL = 55
    SIDE = 54  # 1=Buy, 2=Sell
    ORDER_QTY = 38
    ORD_TYPE = 40  # 1=Market, 2=Limit
    PRICE = 44
    TIME_IN_FORCE = 59  # 0=Day, 1=GTC, 3=IOC, 4=FOK
    ORD_STATUS = 39  # 0=New, 1=Partially filled, 2=Filled, 4=Cancelled, 8=Rejected
    EXEC_TYPE = 150
    LEAVES_QTY = 151
    CUM_QTY = 14
    AVG_PX = 6
    TEXT = 58
    TRANSACT_TIME = 60


class FIXMessage:
    def __init__(self, msg_type: str = ""):
        self.fields: dict[int, str] = {}
        if msg_type:
            self.set(FIXTag.MSG_TYPE, msg_type)

    def set(self, tag: int, value: Any) -> None:
        self.fields[tag] = str(value)

    def get(self, tag: int, default: Any = None) -> str | None:
        return self.fields.get(tag, default)

    def get_float(self, tag: int, default: float = 0.0) -> float:
        val = self.fields.get(tag)
        if val is not None:
            try:
                return float(val)
            except ValueError:
                pass
        return default

    def get_int(self, tag: int, default: int = 0) -> int:
        val = self.fields.get(tag)
        if val is not None:
            try:
                return int(val)
            except ValueError:
                pass
        return default

    def encode(
        self, sender: str = "MDRAP", target: str = "BROKER", seq_num: int = 1
    ) -> str:
        # Get version, defaulting to FIX.4.2
        version = self.fields.get(FIXTag.BEGIN_STRING, "FIX.4.2")
        self.set(FIXTag.BEGIN_STRING, version)
        self.set(FIXTag.SENDER_COMP_ID, sender)
        self.set(FIXTag.TARGET_COMP_ID, target)
        self.set(FIXTag.MSG_SEQ_NUM, seq_num)

        # UTC sending time YYYYMMDD-HH:MM:SS.sss
        now = datetime.now(timezone.utc)
        sending_time = now.strftime("%Y%m%d-%H:%M:%S.%f")[:21]
        self.set(FIXTag.SENDING_TIME, sending_time)

        # Header tags that are placed at the beginning
        header_tags = [FIXTag.BEGIN_STRING, FIXTag.BODY_LENGTH, FIXTag.MSG_TYPE]
        trailer_tags = [FIXTag.CHECK_SUM]

        _msg_type = self.fields.get(FIXTag.MSG_TYPE, "")

        # Order tags for serialization (MsgType, SenderCompID, TargetCompID, MsgSeqNum, SendingTime, then the rest)
        ordered_body_tags = [
            FIXTag.MSG_TYPE,
            FIXTag.SENDER_COMP_ID,
            FIXTag.TARGET_COMP_ID,
            FIXTag.MSG_SEQ_NUM,
            FIXTag.SENDING_TIME,
        ]

        body_parts = []
        for tag in ordered_body_tags:
            body_parts.append(f"{tag}={self.fields[tag]}")

        for tag, value in sorted(self.fields.items()):
            if (
                tag not in header_tags
                and tag not in trailer_tags
                and tag not in ordered_body_tags
            ):
                body_parts.append(f"{tag}={value}")

        body_str = SOH.join(body_parts) + SOH
        self.set(FIXTag.BODY_LENGTH, len(body_str))

        msg_without_checksum = f"{FIXTag.BEGIN_STRING}={version}{SOH}{FIXTag.BODY_LENGTH}={len(body_str)}{SOH}{body_str}"

        if fastpath is not None:
            c_sum = fastpath.fast_fix_checksum(msg_without_checksum)
            checksum = (
                c_sum
                if c_sum is not None
                else sum(ord(c) for c in msg_without_checksum) % 256
            )
        else:
            checksum = sum(ord(c) for c in msg_without_checksum) % 256
        self.set(FIXTag.CHECK_SUM, f"{checksum:03d}")

        return f"{msg_without_checksum}{FIXTag.CHECK_SUM}={checksum:03d}{SOH}"

    @classmethod
    def parse(cls, raw: str) -> "FIXMessage":
        delimiter = "|" if "|" in raw and SOH not in raw else SOH
        parts = raw.strip().strip(delimiter).split(delimiter)

        msg = cls()
        for part in parts:
            if "=" not in part:
                continue
            tag_str, value = part.split("=", 1)
            try:
                tag = int(tag_str)
                msg.set(tag, value)
            except ValueError:
                pass

        return msg


class FIXSession:
    """Manages FIX 4.2 session state, sequence numbers, and message flow."""

    def __init__(
        self, sender_comp_id: str, target_comp_id: str, fix_version: str = "FIX.4.2"
    ):
        self.sender = sender_comp_id
        self.target = target_comp_id
        self.version = fix_version
        self.out_seq = 1
        self.in_seq = 0
        self.is_logged_on = False
        self.heartbeat_interval = 30

    def _prepare_and_encode(self, msg: FIXMessage) -> FIXMessage:
        msg.set(FIXTag.BEGIN_STRING, self.version)
        msg.set(FIXTag.MSG_SEQ_NUM, self.out_seq)
        self.out_seq += 1
        return msg

    def create_logon(self, heartbeat_int: int = 30) -> FIXMessage:
        self.heartbeat_interval = heartbeat_int
        msg = FIXMessage("A")
        msg.set(108, heartbeat_int)  # HeartBtInt
        msg.set(98, 0)  # EncryptMethod
        return self._prepare_and_encode(msg)

    def create_heartbeat(self, test_req_id: str = "") -> FIXMessage:
        msg = FIXMessage("0")
        if test_req_id:
            msg.set(112, test_req_id)
        return self._prepare_and_encode(msg)

    def create_new_order_single(
        self,
        cl_ord_id: str,
        symbol: str,
        side: str,
        qty: float,
        ord_type: str = "1",
        price: float | None = None,
    ) -> FIXMessage:
        msg = FIXMessage("D")
        msg.set(FIXTag.CL_ORD_ID, cl_ord_id)
        msg.set(FIXTag.SYMBOL, symbol)
        msg.set(FIXTag.SIDE, side)
        msg.set(FIXTag.ORDER_QTY, qty)
        msg.set(FIXTag.ORD_TYPE, ord_type)
        if price is not None and ord_type == "2":
            msg.set(FIXTag.PRICE, price)
        msg.set(
            FIXTag.TRANSACT_TIME,
            datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:21],
        )
        return self._prepare_and_encode(msg)

    def create_order_cancel(
        self, cl_ord_id: str, orig_cl_ord_id: str, symbol: str, side: str
    ) -> FIXMessage:
        msg = FIXMessage("F")
        msg.set(FIXTag.CL_ORD_ID, cl_ord_id)
        msg.set(41, orig_cl_ord_id)  # OrigClOrdID
        msg.set(FIXTag.SYMBOL, symbol)
        msg.set(FIXTag.SIDE, side)
        msg.set(
            FIXTag.TRANSACT_TIME,
            datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:21],
        )
        return self._prepare_and_encode(msg)

    def process_incoming(self, raw_msg: str) -> FIXMessage:
        msg = FIXMessage.parse(raw_msg)
        seq_num = msg.get_int(FIXTag.MSG_SEQ_NUM)

        if seq_num > 0:
            self.in_seq = seq_num

        msg_type = msg.get(FIXTag.MSG_TYPE)
        sender = msg.get(FIXTag.SENDER_COMP_ID)
        target = msg.get(FIXTag.TARGET_COMP_ID)

        # Validate CompID against session parameters
        comp_id_valid = True
        if sender and sender != self.target:
            comp_id_valid = False
        if target and target != self.sender:
            comp_id_valid = False

        if msg_type == "A":
            self.is_logged_on = comp_id_valid
        elif msg_type == "5":  # Logout
            self.is_logged_on = False

        return msg
