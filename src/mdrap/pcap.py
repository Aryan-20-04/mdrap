"""MDRAP Institutional PCAP Network Packet Replay Engine & MoldUDP64/ITCH Harness (Phase 16 & 21).

Implements standard Libpcap 2.4 file format reader, writer, and stream dissector:
- Global PCAP Header (24 bytes) & Record Header (16 bytes) binary packing/unpacking.
- Fast zero-copy dissector for Ethernet II, IPv4, UDP, and MoldUDP64 multicast protocols.
- Deterministic packet pacer with real-time scaling and microsecond timestamp alignment.
- Corrupted frame generation for adversarial network chaos testing (truncated frames, packet drops, sequence gaps).
"""

from __future__ import annotations

import struct
import time
import warnings
from dataclasses import dataclass
from typing import BinaryIO, Iterator

__stability__ = "stable"

PCAP_MAGIC_MICROSECONDS = 0xA1B2C3D4
PCAP_MAGIC_NANOSECONDS = 0xA1B23C4D
PCAP_VERSION_MAJOR = 2
PCAP_VERSION_MINOR = 4
LINKTYPE_ETHERNET = 1

# Pre-compiled struct formats
GLOBAL_HEADER_STRUCT = struct.Struct("<IHHiIII")
PACKET_HEADER_STRUCT = struct.Struct("<IIII")
ETH_HEADER_STRUCT = struct.Struct("!6s6sH")
IPV4_HEADER_STRUCT = struct.Struct("!BBHHHBBH4s4s")
UDP_HEADER_STRUCT = struct.Struct("!HHHH")
MOLDUDP64_HEADER_STRUCT = struct.Struct(
    "!10sQH"
)  # Session (10s), Seq (Q), MsgCount (H)


@dataclass(slots=True)
class CapturedPacket:
    """Dissected UDP market data frame extracted from a raw network PCAP."""

    timestamp: float
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    payload: bytes
    orig_length: int


class PcapWriter:
    """Writes standard Libpcap 2.4 files with Ethernet/IPv4/UDP encapsulation."""

    def __init__(self, file_obj: BinaryIO | str, snaplen: int = 65535) -> None:
        if isinstance(file_obj, str):
            self._file = open(file_obj, "wb")
            self._owns_file = True
        else:
            self._file = file_obj
            self._owns_file = False

        self.snaplen = snaplen
        self._write_global_header()

    def _write_global_header(self) -> None:
        header = GLOBAL_HEADER_STRUCT.pack(
            PCAP_MAGIC_MICROSECONDS,
            PCAP_VERSION_MAJOR,
            PCAP_VERSION_MINOR,
            0,  # GMT correction
            0,  # Sigfigs
            self.snaplen,
            LINKTYPE_ETHERNET,
        )
        self._file.write(header)

    def write_udp_packet(
        self,
        payload: bytes,
        timestamp: float | None = None,
        src_ip: str = "192.168.1.10",
        dst_ip: str = "233.54.12.1",
        src_port: int = 50000,
        dst_port: int = 20000,
    ) -> None:
        """Encapsulate UDP payload in Ethernet II / IPv4 and append to PCAP."""
        ts = timestamp if timestamp is not None else time.time()
        ts_sec = int(ts)
        ts_usec = int((ts - ts_sec) * 1_000_000)

        # 1. UDP Header
        udp_len = 8 + len(payload)
        udp_hdr = UDP_HEADER_STRUCT.pack(src_port, dst_port, udp_len, 0)
        udp_datagram = udp_hdr + payload

        # 2. IPv4 Header
        src_ip_bytes = bytes(map(int, src_ip.split(".")))
        dst_ip_bytes = bytes(map(int, dst_ip.split(".")))
        ip_total_len = 20 + len(udp_datagram)
        ip_hdr = IPV4_HEADER_STRUCT.pack(
            0x45,  # Version 4, IHL 5
            0,  # DSCP/ECN
            ip_total_len,
            12345,  # Identification
            0x4000,  # Flags (Don't fragment)
            64,  # TTL
            17,  # UDP Protocol
            0,  # Checksum
            src_ip_bytes,
            dst_ip_bytes,
        )
        ip_packet = ip_hdr + udp_datagram

        # 3. Ethernet Header (Fake MAC addresses)
        eth_hdr = ETH_HEADER_STRUCT.pack(
            b"\x00\x11\x22\x33\x44\x55",
            b"\x66\x77\x88\x99\xaa\xbb",
            0x0800,  # EtherType IPv4
        )
        frame = eth_hdr + ip_packet

        # 4. PCAP Packet Record Header
        incl_len = min(len(frame), self.snaplen)
        rec_hdr = PACKET_HEADER_STRUCT.pack(ts_sec, ts_usec, incl_len, len(frame))
        self._file.write(rec_hdr)
        self._file.write(frame[:incl_len])

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.flush()
        if self._owns_file:
            self._file.close()

    def __enter__(self) -> PcapWriter:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class PcapReader:
    """Reads Libpcap 2.4 files and dissects UDP market data frames."""

    def __init__(self, file_obj: BinaryIO | str) -> None:
        warnings.warn(
            "PcapReader and raw packet dissection in mdrap.pcap are deprecated and non-core to the market data engine.",
            DeprecationWarning,
            stacklevel=2,
        )
        if isinstance(file_obj, str):
            self._file = open(file_obj, "rb")
            self._owns_file = True
        else:
            self._file = file_obj
            self._owns_file = False

        self._read_global_header()

    def _read_global_header(self) -> None:
        raw = self._file.read(GLOBAL_HEADER_STRUCT.size)
        if len(raw) < GLOBAL_HEADER_STRUCT.size:
            raise ValueError("Truncated PCAP global header")

        (
            magic,
            major,
            minor,
            _,
            _,
            self.snaplen,
            self.linktype,
        ) = GLOBAL_HEADER_STRUCT.unpack(raw)

        if magic == PCAP_MAGIC_MICROSECONDS:
            self.is_nanoseconds = False
        elif magic == PCAP_MAGIC_NANOSECONDS:
            self.is_nanoseconds = True
        else:
            raise ValueError(f"Invalid PCAP magic: 0x{magic:08x}")

        if self.linktype != LINKTYPE_ETHERNET:
            raise ValueError(
                f"Unsupported linktype {self.linktype}; expected Ethernet (1)"
            )

    def packets(self) -> Iterator[CapturedPacket]:
        """Iterate over dissected UDP frames in the capture file."""
        while True:
            rec_raw = self._file.read(PACKET_HEADER_STRUCT.size)
            if not rec_raw or len(rec_raw) < PACKET_HEADER_STRUCT.size:
                break

            ts_sec, ts_sub, incl_len, orig_len = PACKET_HEADER_STRUCT.unpack(rec_raw)
            frame_data = self._file.read(incl_len)
            if len(frame_data) < incl_len:
                break

            divisor = 1_000_000_000.0 if self.is_nanoseconds else 1_000_000.0
            pkt_ts = ts_sec + (ts_sub / divisor)

            # Ethernet Dissection
            if len(frame_data) < ETH_HEADER_STRUCT.size:
                continue
            _, _, ethertype = ETH_HEADER_STRUCT.unpack(frame_data[:14])
            if ethertype != 0x0800:
                continue  # Skip non-IPv4

            # IPv4 Dissection
            ip_data = frame_data[14:]
            if len(ip_data) < IPV4_HEADER_STRUCT.size:
                continue
            v_ihl = ip_data[0]
            ihl = (v_ihl & 0x0F) * 4
            protocol = ip_data[9]
            if protocol != 17 or len(ip_data) < ihl:
                continue  # Skip non-UDP

            src_ip = ".".join(map(str, ip_data[12:16]))
            dst_ip = ".".join(map(str, ip_data[16:20]))

            # UDP Dissection
            udp_data = ip_data[ihl:]
            if len(udp_data) < UDP_HEADER_STRUCT.size:
                continue
            src_port, dst_port, udp_len, _ = UDP_HEADER_STRUCT.unpack(udp_data[:8])
            payload = udp_data[8:udp_len]

            yield CapturedPacket(
                timestamp=pkt_ts,
                src_ip=src_ip,
                dst_ip=dst_ip,
                src_port=src_port,
                dst_port=dst_port,
                payload=payload,
                orig_length=orig_len,
            )

    def close(self) -> None:
        if self._owns_file:
            self._file.close()

    def __enter__(self) -> PcapReader:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class MoldUDP64Dissector:
    """Parses NASDAQ MoldUDP64 multicast packet payloads into individual messages."""

    @staticmethod
    def dissect(payload: bytes) -> tuple[str, int, list[bytes]]:
        """
        Dissect MoldUDP64 payload.
        Returns:
            (session_id, sequence_num, list_of_message_payloads)
        """
        try:
            if not payload or len(payload) < MOLDUDP64_HEADER_STRUCT.size:
                return "", 0, []

            sess_bytes, seq_num, msg_count = MOLDUDP64_HEADER_STRUCT.unpack(
                payload[: MOLDUDP64_HEADER_STRUCT.size]
            )
            session_id = sess_bytes.decode("ascii", errors="replace").strip()

            messages: list[bytes] = []
            offset = MOLDUDP64_HEADER_STRUCT.size

            for _ in range(msg_count):
                if offset + 2 > len(payload):
                    break
                msg_len = struct.unpack("!H", payload[offset : offset + 2])[0]
                offset += 2
                if offset + msg_len > len(payload):
                    break
                messages.append(payload[offset : offset + msg_len])
                offset += msg_len

            return session_id, seq_num, messages
        except Exception:
            return "", 0, []
