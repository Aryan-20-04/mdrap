"""
Market Venue Registry and Global Exchange Microstructure Engine.

Defines exchange metadata, trading session schedules, currency conventions,
and microstructure parameters for Indian (NSE/BSE), German (Deutsche Börse/Xetra/Eurex),
Japanese (TSE/JPX), US (Nasdaq/NYSE), and other global financial venues.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple


class MarketPhase(str, Enum):
    PRE_OPEN = "PRE_OPEN"  # Call auction / order collection
    CONTINUOUS = "CONTINUOUS"  # Continuous double auction (Zaraba)
    VOLATILITY_HALT = "VOLATILITY_HALT"  # Volatility interruption / circuit halt
    CLOSING_AUCTION = "CLOSING_AUCTION"  # Closing cross / Itayose
    POST_CLOSE = "POST_CLOSE"  # Reporting / settlement
    CLOSED = "CLOSED"  # Market closed


class TickSizeModel(str, Enum):
    FIXED_0_01 = "FIXED_0_01"  # Standard US penny tick ($0.01)
    NSE_DYNAMIC = (
        "NSE_DYNAMIC"  # Indian NSE dynamic tick (0.05 INR, 0.01 INR for sub-250)
    )
    MIFID2_RTS28 = "MIFID2_RTS28"  # European MiFID II liquidity band tiers
    TSE_TIERED = "TSE_TIERED"  # Japanese TSE TOPIX100 fractional & standard yen tiers


@dataclass(slots=True, frozen=True)
class MarketVenue:
    mic: str  # ISO 10383 Market Identifier Code
    name: str
    country: str  # ISO 3166-1 alpha-2
    flag: str  # Unicode emoji flag
    currency: str  # ISO 4217 currency code
    currency_symbol: str  # Localized currency glyph
    timezone_name: str
    utc_offset_hours: float  # Standard offset in hours
    open_time_utc_hour: float  # Market continuous open (UTC fractional hours)
    close_time_utc_hour: float  # Market continuous close (UTC fractional hours)
    benchmark_index: str  # Ticker of primary index
    index_name: str  # Human-readable index name
    tick_size_model: TickSizeModel = TickSizeModel.FIXED_0_01
    pre_open_utc_hour: Optional[float] = None
    closing_auction_utc_hour: Optional[float] = None
    circuit_limit_pct: float = 10.0  # Stock / index standard circuit band


# ---------------------------------------------------------------------------
# Global Venue Registry Definitions
# ---------------------------------------------------------------------------

GLOBAL_VENUES: Dict[str, MarketVenue] = {
    # 1. Indian Markets (NSE / BSE)
    # Open: 09:15-15:30 IST (UTC+5:30) -> 03:45 to 10:00 UTC
    # Pre-open: 09:00-09:08 IST -> 03:30 to 03:38 UTC
    "XNSE": MarketVenue(
        mic="XNSE",
        name="National Stock Exchange of India",
        country="IN",
        flag="🇮🇳",
        currency="INR",
        currency_symbol="₹",
        timezone_name="Asia/Kolkata",
        utc_offset_hours=5.5,
        open_time_utc_hour=3.75,  # 03:45 UTC = 09:15 IST
        close_time_utc_hour=10.0,  # 10:00 UTC = 15:30 IST
        pre_open_utc_hour=3.5,  # 03:30 UTC = 09:00 IST
        closing_auction_utc_hour=9.833,  # 09:50 UTC = 15:20 IST
        benchmark_index="NIFTY",
        index_name="Nifty 50",
        tick_size_model=TickSizeModel.NSE_DYNAMIC,
        circuit_limit_pct=10.0,
    ),
    "XBOM": MarketVenue(
        mic="XBOM",
        name="BSE India (Bombay Stock Exchange)",
        country="IN",
        flag="🇮🇳",
        currency="INR",
        currency_symbol="₹",
        timezone_name="Asia/Kolkata",
        utc_offset_hours=5.5,
        open_time_utc_hour=3.75,
        close_time_utc_hour=10.0,
        pre_open_utc_hour=3.5,
        closing_auction_utc_hour=9.833,
        benchmark_index="SENSEX",
        index_name="BSE SENSEX",
        tick_size_model=TickSizeModel.NSE_DYNAMIC,
        circuit_limit_pct=10.0,
    ),
    # 2. German Markets (Deutsche Börse / Xetra / Eurex)
    # Continuous: 09:00-17:30 CET (UTC+1) -> 08:00 to 16:30 UTC
    "XETR": MarketVenue(
        mic="XETR",
        name="Deutsche Börse Xetra (Frankfurt)",
        country="DE",
        flag="🇩🇪",
        currency="EUR",
        currency_symbol="€",
        timezone_name="Europe/Berlin",
        utc_offset_hours=1.0,
        open_time_utc_hour=8.0,  # 08:00 UTC = 09:00 CET
        close_time_utc_hour=16.5,  # 16:30 UTC = 17:30 CET
        pre_open_utc_hour=7.5,  # 07:30 UTC = 08:30 CET
        closing_auction_utc_hour=16.417,  # 16:25 UTC = 17:25 CET
        benchmark_index="DAX",
        index_name="DAX 40",
        tick_size_model=TickSizeModel.MIFID2_RTS28,
        circuit_limit_pct=5.0,  # Volatility corridor
    ),
    "XEUR": MarketVenue(
        mic="XEUR",
        name="Eurex Derivatives Exchange",
        country="DE",
        flag="🇩🇪",
        currency="EUR",
        currency_symbol="€",
        timezone_name="Europe/Berlin",
        utc_offset_hours=1.0,
        open_time_utc_hour=7.0,  # 07:00 UTC = 08:00 CET
        close_time_utc_hour=21.0,  # 21:00 UTC = 22:00 CET
        benchmark_index="SX5E",
        index_name="EURO STOXX 50",
        tick_size_model=TickSizeModel.MIFID2_RTS28,
        circuit_limit_pct=5.0,
    ),
    # 3. Japanese Markets (Tokyo Stock Exchange / JPX)
    # Open: 09:00-15:30 JST (UTC+9) -> 00:00 to 06:30 UTC
    "XTKS": MarketVenue(
        mic="XTKS",
        name="Tokyo Stock Exchange (JPX)",
        country="JP",
        flag="🇯🇵",
        currency="JPY",
        currency_symbol="¥",
        timezone_name="Asia/Tokyo",
        utc_offset_hours=9.0,
        open_time_utc_hour=0.0,  # 00:00 UTC = 09:00 JST
        close_time_utc_hour=6.5,  # 06:30 UTC = 15:30 JST
        pre_open_utc_hour=23.0,  # 23:00 UTC (-1 day) = 08:00 JST
        closing_auction_utc_hour=6.417,  # 06:25 UTC = 15:25 JST (Itayose)
        benchmark_index="N225",
        index_name="Nikkei 225",
        tick_size_model=TickSizeModel.TSE_TIERED,
        circuit_limit_pct=8.0,
    ),
    "XOSE": MarketVenue(
        mic="XOSE",
        name="Osaka Exchange (JPX Derivatives)",
        country="JP",
        flag="🇯🇵",
        currency="JPY",
        currency_symbol="¥",
        timezone_name="Asia/Tokyo",
        utc_offset_hours=9.0,
        open_time_utc_hour=23.75,  # 08:45 JST
        close_time_utc_hour=6.5,
        benchmark_index="NK225F",
        index_name="Nikkei 225 Futures",
        tick_size_model=TickSizeModel.TSE_TIERED,
        circuit_limit_pct=8.0,
    ),
    # 4. United States (Nasdaq / NYSE)
    # Open: 09:30-16:00 EST (UTC-5) -> 14:30 to 21:00 UTC
    "XNAS": MarketVenue(
        mic="XNAS",
        name="Nasdaq Stock Market",
        country="US",
        flag="🇺🇸",
        currency="USD",
        currency_symbol="$",
        timezone_name="America/New_York",
        utc_offset_hours=-5.0,
        open_time_utc_hour=14.5,  # 14:30 UTC = 09:30 EST
        close_time_utc_hour=21.0,  # 21:00 UTC = 16:00 EST
        pre_open_utc_hour=9.0,  # 09:00 UTC = 04:00 EST (Pre-market)
        closing_auction_utc_hour=20.917,  # Closing cross
        benchmark_index="NDX",
        index_name="NASDAQ 100",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=7.0,  # LULD / Reg NMS
    ),
    "XNYS": MarketVenue(
        mic="XNYS",
        name="New York Stock Exchange",
        country="US",
        flag="🇺🇸",
        currency="USD",
        currency_symbol="$",
        timezone_name="America/New_York",
        utc_offset_hours=-5.0,
        open_time_utc_hour=14.5,
        close_time_utc_hour=21.0,
        pre_open_utc_hour=9.0,
        closing_auction_utc_hour=20.917,
        benchmark_index="SPX",
        index_name="S&P 500",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=7.0,
    ),
    # 5. United Kingdom (London Stock Exchange)
    "XLON": MarketVenue(
        mic="XLON",
        name="London Stock Exchange",
        country="GB",
        flag="🇬🇧",
        currency="GBP",
        currency_symbol="£",
        timezone_name="Europe/London",
        utc_offset_hours=0.0,
        open_time_utc_hour=8.0,  # 08:00 UTC = 08:00 GMT
        close_time_utc_hour=16.5,  # 16:30 UTC = 16:30 GMT
        pre_open_utc_hour=7.833,  # 07:50 UTC
        closing_auction_utc_hour=16.417,
        benchmark_index="UKX",
        index_name="FTSE 100",
        tick_size_model=TickSizeModel.MIFID2_RTS28,
        circuit_limit_pct=8.0,
    ),
    # 6. Hong Kong (HKEX)
    "XHKG": MarketVenue(
        mic="XHKG",
        name="Hong Kong Exchanges and Clearing",
        country="HK",
        flag="🇭🇰",
        currency="HKD",
        currency_symbol="HK$",
        timezone_name="Asia/Hong_Kong",
        utc_offset_hours=8.0,
        open_time_utc_hour=1.5,  # 01:30 UTC = 09:30 HKT
        close_time_utc_hour=8.0,  # 08:00 UTC = 16:00 HKT
        pre_open_utc_hour=1.0,  # 09:00 HKT
        closing_auction_utc_hour=7.833,
        benchmark_index="HSI",
        index_name="Hang Seng Index",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=10.0,
    ),
    # 7. US Commodities & Futures (CME Group)
    "XCME": MarketVenue(
        mic="XCME",
        name="Chicago Mercantile Exchange",
        country="US",
        flag="🇺🇸",
        currency="USD",
        currency_symbol="$",
        timezone_name="America/Chicago",
        utc_offset_hours=-6.0,
        open_time_utc_hour=0.0,
        close_time_utc_hour=23.0,
        benchmark_index="ES",
        index_name="E-mini S&P 500 Futures",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=7.0,
    ),
    "XNYM": MarketVenue(
        mic="XNYM",
        name="New York Mercantile Exchange (NYMEX)",
        country="US",
        flag="🇺🇸",
        currency="USD",
        currency_symbol="$",
        timezone_name="America/New_York",
        utc_offset_hours=-5.0,
        open_time_utc_hour=23.0,
        close_time_utc_hour=22.0,
        benchmark_index="CL",
        index_name="WTI Light Sweet Crude Oil",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=10.0,
    ),
    "XCBT": MarketVenue(
        mic="XCBT",
        name="Chicago Board of Trade (CBOT)",
        country="US",
        flag="🇺🇸",
        currency="USD",
        currency_symbol="$",
        timezone_name="America/Chicago",
        utc_offset_hours=-6.0,
        open_time_utc_hour=1.0,
        close_time_utc_hour=20.0,
        benchmark_index="ZB",
        index_name="U.S. Treasury Bond Futures",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=5.0,
    ),
    "IFEU": MarketVenue(
        mic="IFEU",
        name="ICE Futures Europe",
        country="GB",
        flag="🇬🇧",
        currency="USD",
        currency_symbol="$",
        timezone_name="Europe/London",
        utc_offset_hours=0.0,
        open_time_utc_hour=1.0,
        close_time_utc_hour=23.0,
        benchmark_index="B",
        index_name="Brent Crude Futures",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=10.0,
    ),
    # 8. Singapore (SGX)
    "XSES": MarketVenue(
        mic="XSES",
        name="Singapore Exchange",
        country="SG",
        flag="🇸🇬",
        currency="SGD",
        currency_symbol="S$",
        timezone_name="Asia/Singapore",
        utc_offset_hours=8.0,
        open_time_utc_hour=1.0,
        close_time_utc_hour=9.0,
        benchmark_index="STI",
        index_name="Straits Times Index",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=10.0,
    ),
    # 9. South Korea (KRX)
    "XKRX": MarketVenue(
        mic="XKRX",
        name="Korea Exchange",
        country="KR",
        flag="🇰🇷",
        currency="KRW",
        currency_symbol="₩",
        timezone_name="Asia/Seoul",
        utc_offset_hours=9.0,
        open_time_utc_hour=0.0,
        close_time_utc_hour=6.5,
        benchmark_index="KOSPI200",
        index_name="KOSPI 200",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=10.0,
    ),
    # 10. Taiwan (TWSE)
    "XTWS": MarketVenue(
        mic="XTWS",
        name="Taiwan Stock Exchange",
        country="TW",
        flag="🇹🇼",
        currency="TWD",
        currency_symbol="NT$",
        timezone_name="Asia/Taipei",
        utc_offset_hours=8.0,
        open_time_utc_hour=1.0,
        close_time_utc_hour=5.5,
        benchmark_index="TAIEX",
        index_name="Taiwan Capitalization Weighted Stock Index",
        tick_size_model=TickSizeModel.FIXED_0_01,
        circuit_limit_pct=10.0,
    ),
    # 11. Euronext Paris
    "XPAR": MarketVenue(
        mic="XPAR",
        name="Euronext Paris",
        country="FR",
        flag="🇫🇷",
        currency="EUR",
        currency_symbol="€",
        timezone_name="Europe/Paris",
        utc_offset_hours=1.0,
        open_time_utc_hour=8.0,
        close_time_utc_hour=16.5,
        benchmark_index="PX1",
        index_name="CAC 40",
        tick_size_model=TickSizeModel.MIFID2_RTS28,
        circuit_limit_pct=8.0,
    ),
}

# Aliases mapping informal tags and country codes to canonical MICs
VENUE_ALIASES: Dict[str, str] = {
    # India
    "nse": "XNSE",
    "nifty": "XNSE",
    "india": "XNSE",
    "in": "XNSE",
    "bse": "XBOM",
    "sensex": "XBOM",
    "bombay": "XBOM",
    # Germany / Europe
    "xetra": "XETR",
    "xetr": "XETR",
    "frankfurt": "XETR",
    "dax": "XETR",
    "germany": "XETR",
    "de": "XETR",
    "eurex": "XEUR",
    "xeur": "XEUR",
    # Japan
    "tse": "XTKS",
    "tokyo": "XTKS",
    "japan": "XTKS",
    "jp": "XTKS",
    "jpx": "XTKS",
    "nikkei": "XTKS",
    "ose": "XOSE",
    "osaka": "XOSE",
    # US
    "nasdaq": "XNAS",
    "nas": "XNAS",
    "us": "XNAS",
    "usa": "XNAS",
    "nyse": "XNYS",
    # UK
    "lse": "XLON",
    "london": "XLON",
    "uk": "XLON",
    "ftse": "XLON",
    # Hong Kong
    "hkex": "XHKG",
    "hongkong": "XHKG",
    "hk": "XHKG",
    # Commodities / Futures
    "cme": "XCME",
    "globex": "XCME",
    "nymex": "XNYM",
    "cbot": "XCBT",
    "ice": "IFEU",
    # Singapore
    "sgx": "XSES",
    "singapore": "XSES",
    "sg": "XSES",
    # South Korea
    "krx": "XKRX",
    "korea": "XKRX",
    "kr": "XKRX",
    "kospi": "XKRX",
    # Taiwan
    "twse": "XTWS",
    "taiwan": "XTWS",
    "tw": "XTWS",
    # Euronext Paris
    "euronext": "XPAR",
    "paris": "XPAR",
    "cac": "XPAR",
    "fr": "XPAR",
}


def get_venue(identifier: str) -> MarketVenue:
    """Resolve venue by MIC code or informal alias. Defaults to XNAS if unknown."""
    if not identifier:
        return GLOBAL_VENUES["XNAS"]
    clean = str(identifier).strip().upper()
    if clean in GLOBAL_VENUES:
        return GLOBAL_VENUES[clean]
    lower = identifier.strip().lower()
    if lower in VENUE_ALIASES:
        return GLOBAL_VENUES[VENUE_ALIASES[lower]]
    return GLOBAL_VENUES["XNAS"]


def get_session_phase(
    venue: MarketVenue,
    timestamp: Optional[float] = None,
) -> Tuple[bool, MarketPhase, str]:
    """
    Evaluates exchange trading schedule and determines live market phase.
    Returns: (is_open, phase, human_readable_description)
    """
    if timestamp is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
    else:
        now_utc = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)

    # Weekend check: Saturday=5, Sunday=6
    weekday = now_utc.weekday()
    if weekday in (5, 6):
        return False, MarketPhase.CLOSED, "Closed (Weekend)"

    # Convert UTC time to fractional hour [0.0, 24.0)
    hour_frac = now_utc.hour + now_utc.minute / 60.0 + now_utc.second / 3600.0

    open_h = venue.open_time_utc_hour
    close_h = venue.close_time_utc_hour
    pre_h = venue.pre_open_utc_hour
    close_auc_h = venue.closing_auction_utc_hour

    # Standard daytime market window in UTC (without crossing midnight)
    if open_h < close_h:
        if pre_h is not None and pre_h <= hour_frac < open_h:
            return False, MarketPhase.PRE_OPEN, "Pre-Open Call Auction"
        if close_auc_h is not None and close_auc_h <= hour_frac < close_h:
            return True, MarketPhase.CLOSING_AUCTION, "Closing Auction (Cross)"
        if open_h <= hour_frac < close_h:
            return True, MarketPhase.CONTINUOUS, "Open (Continuous Trading)"
        if close_h <= hour_frac < close_h + 1.0:
            return False, MarketPhase.POST_CLOSE, "Post-Close / Settlement"
        return False, MarketPhase.CLOSED, "Closed"
    else:
        # Venue trading session wraps over UTC midnight (e.g. TSE pre-open at 23:00 UTC)
        if pre_h is not None and (hour_frac >= pre_h or hour_frac < open_h):
            return False, MarketPhase.PRE_OPEN, "Pre-Open Call Auction"
        if close_auc_h is not None and close_auc_h <= hour_frac < close_h:
            return True, MarketPhase.CLOSING_AUCTION, "Closing Auction (Itayose)"
        if hour_frac >= open_h and hour_frac < close_h:
            return True, MarketPhase.CONTINUOUS, "Open (Continuous Trading)"
        return False, MarketPhase.CLOSED, "Closed"


def get_tick_size(
    price: float, model: TickSizeModel = TickSizeModel.FIXED_0_01
) -> float:
    """Computes minimum price movement (tick size) per venue microstructure model."""
    if price <= 0:
        return 0.01

    if model == TickSizeModel.FIXED_0_01:
        return 0.01

    elif model == TickSizeModel.NSE_DYNAMIC:
        # National Stock Exchange of India tick size rules:
        # Stocks <= ₹250 trade at ₹0.01 tick; stocks > ₹250 trade at ₹0.05 tick
        return 0.01 if price <= 250.0 else 0.05

    elif model == TickSizeModel.MIFID2_RTS28:
        # MiFID II Liquidity Band table (standard liquid equities tier)
        if price < 0.50:
            return 0.0001
        elif price < 1.0:
            return 0.0002
        elif price < 2.0:
            return 0.0005
        elif price < 5.0:
            return 0.001
        elif price < 10.0:
            return 0.002
        elif price < 20.0:
            return 0.005
        elif price < 50.0:
            return 0.01
        elif price < 100.0:
            return 0.02
        elif price < 200.0:
            return 0.05
        elif price < 500.0:
            return 0.10
        else:
            return 0.20

    elif model == TickSizeModel.TSE_TIERED:
        # Tokyo Stock Exchange (TOPIX100 Tiered tick sizes)
        if price <= 1000.0:
            return 0.1
        elif price <= 3000.0:
            return 0.5
        elif price <= 10000.0:
            return 1.0
        elif price <= 30000.0:
            return 5.0
        else:
            return 10.0

    return 0.01


def format_currency(amount: float, currency: str = "USD") -> str:
    """
    Formats price or notional with correct currency symbol and regional conventions.
    Indian Rupee (INR) uses the Lakh / Crore numbering system (e.g. ₹1,25,000.00).
    Japanese Yen (JPY) displays zero decimal places (e.g. ¥3,200).
    """
    curr = currency.upper().strip()
    sym = {
        "INR": "₹",
        "EUR": "€",
        "JPY": "¥",
        "USD": "$",
        "GBP": "£",
        "HKD": "HK$",
        "SGD": "S$",
        "KRW": "₩",
        "TWD": "NT$",
    }.get(curr, f"{curr} ")

    is_negative = amount < 0
    abs_amt = abs(amount)

    if curr == "JPY":
        formatted = f"{abs_amt:,.0f}"
    elif curr == "INR":
        # Indian lakh/crore formatting: 1,23,45,678.90
        parts = f"{abs_amt:.2f}".split(".")
        int_str = parts[0]
        dec_str = parts[1]
        if len(int_str) <= 3:
            num_str = int_str
        else:
            last_three = int_str[-3:]
            remaining = int_str[:-3]
            groups = []
            while len(remaining) > 2:
                groups.append(remaining[-2:])
                remaining = remaining[:-2]
            if remaining:
                groups.append(remaining)
            groups.reverse()
            num_str = ",".join(groups) + "," + last_three
        formatted = f"{num_str}.{dec_str}"
    else:
        formatted = f"{abs_amt:,.2f}"

    prefix = "-" if is_negative else ""
    return f"{prefix}{sym}{formatted}"
