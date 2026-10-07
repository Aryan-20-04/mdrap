"""
MDRAP Universal Symbology Normalizer.

Maps local, regional, and vendor ticker symbologies (Bloomberg tickers,
Reuters Instrument Codes - RICs, ISINs, Yahoo Finance suffixes) into
canonical MDRAP instrument IDs with exchange MIC and currency resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

__stability__ = "stable"


@dataclass(slots=True, frozen=True)
class SymbolInfo:
    canonical_id: str  # e.g. 'RELIANCE.NS', 'SAP.DE', '7203.T', 'AAPL'
    ticker: str  # Base ticker e.g. 'RELIANCE', 'SAP', '7203', 'AAPL'
    venue_mic: str  # ISO MIC e.g. 'XNSE', 'XETR', 'XTKS', 'XNAS'
    currency: str  # ISO 4217 currency e.g. 'INR', 'EUR', 'JPY', 'USD'
    country: str  # 'IN', 'DE', 'JP', 'US', 'GB'
    name: str  # Full company / asset name
    ric: str  # Reuters Instrument Code
    bloomberg: str  # Bloomberg ticker
    isin: str  # International Securities Identification Number
    figi: str = ""  # Financial Instrument Global Identifier (OpenFIGI)


# Reference directory of high-liquidity global benchmark constituents
GLOBAL_SECURITY_DIRECTORY: Dict[str, SymbolInfo] = {
    # -----------------------------------------------------------------------
    # 1. India (NSE Nifty 50 constituents)
    # -----------------------------------------------------------------------
    "RELIANCE": SymbolInfo(
        canonical_id="RELIANCE.NS",
        ticker="RELIANCE",
        venue_mic="XNSE",
        currency="INR",
        country="IN",
        name="Reliance Industries Ltd",
        ric="RELI.NS",
        bloomberg="RELIANCE:IN",
        isin="INE002A01018",
    ),
    "TCS": SymbolInfo(
        canonical_id="TCS.NS",
        ticker="TCS",
        venue_mic="XNSE",
        currency="INR",
        country="IN",
        name="Tata Consultancy Services Ltd",
        ric="TCS.NS",
        bloomberg="TCS:IN",
        isin="INE467B01029",
    ),
    "HDFCBANK": SymbolInfo(
        canonical_id="HDFCBANK.NS",
        ticker="HDFCBANK",
        venue_mic="XNSE",
        currency="INR",
        country="IN",
        name="HDFC Bank Ltd",
        ric="HDBK.NS",
        bloomberg="HDFCB:IN",
        isin="INE040A01034",
    ),
    "INFY": SymbolInfo(
        canonical_id="INFY.NS",
        ticker="INFY",
        venue_mic="XNSE",
        currency="INR",
        country="IN",
        name="Infosys Ltd",
        ric="INFY.NS",
        bloomberg="INFO:IN",
        isin="INE009A01021",
    ),
    "ICICIBANK": SymbolInfo(
        canonical_id="ICICIBANK.NS",
        ticker="ICICIBANK",
        venue_mic="XNSE",
        currency="INR",
        country="IN",
        name="ICICI Bank Ltd",
        ric="ICBK.NS",
        bloomberg="ICICIBC:IN",
        isin="INE090A01021",
    ),
    "TATAMOTORS": SymbolInfo(
        canonical_id="TATAMOTORS.NS",
        ticker="TATAMOTORS",
        venue_mic="XNSE",
        currency="INR",
        country="IN",
        name="Tata Motors Ltd",
        ric="TAMO.NS",
        bloomberg="TTMT:IN",
        isin="INE155A01022",
    ),
    "NIFTY": SymbolInfo(
        canonical_id="^NSEI",
        ticker="NIFTY",
        venue_mic="XNSE",
        currency="INR",
        country="IN",
        name="Nifty 50 Index",
        ric=".NSEI",
        bloomberg="NIFTY:IND",
        isin="INX000000001",
    ),
    "BANKNIFTY": SymbolInfo(
        canonical_id="^NSEBANK",
        ticker="BANKNIFTY",
        venue_mic="XNSE",
        currency="INR",
        country="IN",
        name="Nifty Bank Index",
        ric=".NSEBANK",
        bloomberg="NSEBANK:IND",
        isin="INX000000002",
    ),
    # -----------------------------------------------------------------------
    # 2. Germany (Deutsche Börse Xetra DAX 40 constituents)
    # -----------------------------------------------------------------------
    "SAP": SymbolInfo(
        canonical_id="SAP.DE",
        ticker="SAP",
        venue_mic="XETR",
        currency="EUR",
        country="DE",
        name="SAP SE",
        ric="SAPG.DE",
        bloomberg="SAP:GY",
        isin="DE0007164600",
    ),
    "SIE": SymbolInfo(
        canonical_id="SIE.DE",
        ticker="SIE",
        venue_mic="XETR",
        currency="EUR",
        country="DE",
        name="Siemens AG",
        ric="SIEGn.DE",
        bloomberg="SIE:GY",
        isin="DE0007236101",
    ),
    "BMW": SymbolInfo(
        canonical_id="BMW.DE",
        ticker="BMW",
        venue_mic="XETR",
        currency="EUR",
        country="DE",
        name="Bayerische Motoren Werke AG (BMW)",
        ric="BMWG.DE",
        bloomberg="BMW:GY",
        isin="DE0005190003",
    ),
    "VOW3": SymbolInfo(
        canonical_id="VOW3.DE",
        ticker="VOW3",
        venue_mic="XETR",
        currency="EUR",
        country="DE",
        name="Volkswagen AG Vorzug",
        ric="VOWG_p.DE",
        bloomberg="VOW3:GY",
        isin="DE0007664039",
    ),
    "ALV": SymbolInfo(
        canonical_id="ALV.DE",
        ticker="ALV",
        venue_mic="XETR",
        currency="EUR",
        country="DE",
        name="Allianz SE",
        ric="ALVG.DE",
        bloomberg="ALV:GY",
        isin="DE0008404005",
    ),
    "MBG": SymbolInfo(
        canonical_id="MBG.DE",
        ticker="MBG",
        venue_mic="XETR",
        currency="EUR",
        country="DE",
        name="Mercedes-Benz Group AG",
        ric="MBGn.DE",
        bloomberg="MBG:GY",
        isin="DE0007100000",
    ),
    "DAX": SymbolInfo(
        canonical_id="^GDAXI",
        ticker="DAX",
        venue_mic="XETR",
        currency="EUR",
        country="DE",
        name="DAX 40 Index",
        ric=".GDAXI",
        bloomberg="DAX:IND",
        isin="DE0008469008",
    ),
    # -----------------------------------------------------------------------
    # 3. Japan (Tokyo Stock Exchange Nikkei 225 constituents)
    # -----------------------------------------------------------------------
    "7203": SymbolInfo(
        canonical_id="7203.T",
        ticker="7203",
        venue_mic="XTKS",
        currency="JPY",
        country="JP",
        name="Toyota Motor Corp",
        ric="7203.T",
        bloomberg="7203:JP",
        isin="JP3633400001",
    ),
    "6758": SymbolInfo(
        canonical_id="6758.T",
        ticker="6758",
        venue_mic="XTKS",
        currency="JPY",
        country="JP",
        name="Sony Group Corp",
        ric="6758.T",
        bloomberg="6758:JP",
        isin="JP3435000009",
    ),
    "9984": SymbolInfo(
        canonical_id="9984.T",
        ticker="9984",
        venue_mic="XTKS",
        currency="JPY",
        country="JP",
        name="SoftBank Group Corp",
        ric="9984.T",
        bloomberg="9984:JP",
        isin="JP3436100006",
    ),
    "8306": SymbolInfo(
        canonical_id="8306.T",
        ticker="8306",
        venue_mic="XTKS",
        currency="JPY",
        country="JP",
        name="Mitsubishi UFJ Financial Group Inc",
        ric="8306.T",
        bloomberg="8306:JP",
        isin="JP3902900004",
    ),
    "6861": SymbolInfo(
        canonical_id="6861.T",
        ticker="6861",
        venue_mic="XTKS",
        currency="JPY",
        country="JP",
        name="Keyence Corp",
        ric="6861.T",
        bloomberg="6861:JP",
        isin="JP3236200006",
    ),
    "N225": SymbolInfo(
        canonical_id="^N225",
        ticker="N225",
        venue_mic="XTKS",
        currency="JPY",
        country="JP",
        name="Nikkei 225 Average",
        ric=".N225",
        bloomberg="NKY:IND",
        isin="XC0009692440",
    ),
    # -----------------------------------------------------------------------
    # 4. United States (Nasdaq / NYSE)
    # -----------------------------------------------------------------------
    "AAPL": SymbolInfo(
        canonical_id="AAPL",
        ticker="AAPL",
        venue_mic="XNAS",
        currency="USD",
        country="US",
        name="Apple Inc",
        ric="AAPL.O",
        bloomberg="AAPL:US",
        isin="US0378331005",
    ),
    "NVDA": SymbolInfo(
        canonical_id="NVDA",
        ticker="NVDA",
        venue_mic="XNAS",
        currency="USD",
        country="US",
        name="NVIDIA Corporation",
        ric="NVDA.O",
        bloomberg="NVDA:US",
        isin="US67066G1040",
    ),
    "MSFT": SymbolInfo(
        canonical_id="MSFT",
        ticker="MSFT",
        venue_mic="XNAS",
        currency="USD",
        country="US",
        name="Microsoft Corporation",
        ric="MSFT.O",
        bloomberg="MSFT:US",
        isin="US5949181045",
    ),
    "SPY": SymbolInfo(
        canonical_id="SPY",
        ticker="SPY",
        venue_mic="XNYS",
        currency="USD",
        country="US",
        name="SPDR S&P 500 ETF Trust",
        ric="SPY.P",
        bloomberg="SPY:US",
        isin="US78462F1030",
    ),
    "META": SymbolInfo(
        canonical_id="META",
        ticker="META",
        venue_mic="XNAS",
        currency="USD",
        country="US",
        name="Meta Platforms Inc",
        ric="META.O",
        bloomberg="META:US",
        isin="US30303M1027",
    ),
    "FB": SymbolInfo(
        canonical_id="FB",
        ticker="FB",
        venue_mic="XNAS",
        currency="USD",
        country="US",
        name="Meta Platforms Inc (fka Facebook)",
        ric="FB.O",
        bloomberg="FB:US",
        isin="US30303M1027",
    ),
    # -----------------------------------------------------------------------
    # 5. United Kingdom (London Stock Exchange)
    # -----------------------------------------------------------------------
    "SHEL": SymbolInfo(
        canonical_id="SHEL.L",
        ticker="SHEL",
        venue_mic="XLON",
        currency="GBP",
        country="GB",
        name="Shell plc",
        ric="SHEL.L",
        bloomberg="SHEL:LN",
        isin="GB00BP6MXD84",
    ),
    "AZN": SymbolInfo(
        canonical_id="AZN.L",
        ticker="AZN",
        venue_mic="XLON",
        currency="GBP",
        country="GB",
        name="AstraZeneca PLC",
        ric="AZN.L",
        bloomberg="AZN:LN",
        isin="GB0009895292",
    ),
    # -----------------------------------------------------------------------
    # 6. Global Benchmark Commodity & Futures
    # -----------------------------------------------------------------------
    "ES": SymbolInfo(
        canonical_id="ES.CME",
        ticker="ES",
        venue_mic="XCME",
        currency="USD",
        country="US",
        name="E-mini S&P 500 Futures",
        ric="ESc1",
        bloomberg="ESA:INDEX",
        isin="",
    ),
    "CL": SymbolInfo(
        canonical_id="CL.NYM",
        ticker="CL",
        venue_mic="XNYM",
        currency="USD",
        country="US",
        name="WTI Light Sweet Crude Oil Futures",
        ric="CLc1",
        bloomberg="CLA:COMDTY",
        isin="",
    ),
    "BRENT": SymbolInfo(
        canonical_id="BRENT.ICE",
        ticker="BRENT",
        venue_mic="IFEU",
        currency="USD",
        country="GB",
        name="Brent Crude Futures",
        ric="LCOc1",
        bloomberg="COA:COMDTY",
        isin="",
    ),
}

# Add alias mappings for common names and variations
ALIASES_TO_TICKER: Dict[str, str] = {
    # Japan names to 4-digit code
    "TOYOTA": "7203",
    "SONY": "6758",
    "SOFTBANK": "9984",
    "MUFG": "8306",
    "KEYENCE": "6861",
    "NIKKEI": "N225",
    # German names
    "VOLKSWAGEN": "VOW3",
    "ALLIANZ": "ALV",
    "SIEMENS": "SIE",
    "MERCEDES": "MBG",
    # India names
    "RELIANCEIND": "RELIANCE",
    "TATA": "TATAMOTORS",
    "HDFC": "HDFCBANK",
    "INFOSYS": "INFY",
    "ICICI": "ICICIBANK",
}

# Historical corporate actions ticker changes: (old_symbol, new_symbol, change_date)
HISTORICAL_TICKER_CHANGES: list[tuple[str, str, str]] = [
    ("FB", "META", "2022-06-09"),
    ("TWTR", "X", "2023-07-23"),
    ("ANTM", "ELV", "2022-06-28"),
    ("SQ", "BLOCK", "2021-12-01"),
]


def resolve_symbol(
    symbol: str,
    default_venue: str = "XNAS",
    as_of_date: str | None = None,
) -> SymbolInfo:
    """
    Resolves any user-supplied ticker, ISIN, RIC, or Yahoo ticker into canonical SymbolInfo.
    If as_of_date is provided (YYYY-MM-DD), maps historical corporate actions / ticker renames
    to eliminate lookahead bias (Invariant Q5).
    """
    raw = str(symbol).strip().upper()

    if as_of_date:
        date_str = str(as_of_date).strip()[:10]
        for old_sym, new_sym, change_date in HISTORICAL_TICKER_CHANGES:
            if raw == new_sym and date_str < change_date:
                raw = old_sym
                break
            elif raw == old_sym and date_str >= change_date:
                raw = new_sym
                break

    # Direct directory match
    if raw in GLOBAL_SECURITY_DIRECTORY:
        return GLOBAL_SECURITY_DIRECTORY[raw]

    # OpenFIGI or ISIN 12-character global identifier resolution
    if len(raw) == 12:
        for info in GLOBAL_SECURITY_DIRECTORY.values():
            if info.figi and info.figi == raw:
                return info
            if info.isin and info.isin == raw:
                return info

    # Check named alias
    if raw in ALIASES_TO_TICKER:
        mapped_sym = ALIASES_TO_TICKER[raw]
        if mapped_sym in GLOBAL_SECURITY_DIRECTORY:
            return GLOBAL_SECURITY_DIRECTORY[mapped_sym]

    # Handle Yahoo / Exchange Suffixes
    if len(raw) > 3 and raw.endswith(".NS"):
        base = raw[:-3]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XNSE",
            currency="INR",
            country="IN",
            name=f"{base} (NSE India)",
            ric=raw,
            bloomberg=f"{base}:IN",
            isin="",
        )
    elif len(raw) > 3 and raw.endswith(".BO"):
        base = raw[:-3]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XBOM",
            currency="INR",
            country="IN",
            name=f"{base} (BSE India)",
            ric=raw,
            bloomberg=f"{base}:IN",
            isin="",
        )
    elif len(raw) > 3 and raw.endswith(".DE"):
        base = raw[:-3]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XETR",
            currency="EUR",
            country="DE",
            name=f"{base} (Deutsche Börse Xetra)",
            ric=f"{base}.DE",
            bloomberg=f"{base}:GY",
            isin="",
        )
    elif len(raw) > 2 and raw.endswith(".T"):
        base = raw[:-2]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XTKS",
            currency="JPY",
            country="JP",
            name=f"{base} (Tokyo Stock Exchange)",
            ric=raw,
            bloomberg=f"{base}:JP",
            isin="",
        )
    elif len(raw) > 2 and raw.endswith(".L"):
        base = raw[:-2]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XLON",
            currency="GBP",
            country="GB",
            name=f"{base} (London Stock Exchange)",
            ric=raw,
            bloomberg=f"{base}:LN",
            isin="",
        )
    elif len(raw) > 3 and raw.endswith(".HK"):
        base = raw[:-3]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XHKG",
            currency="HKD",
            country="HK",
            name=f"{base} (Hong Kong Exchange)",
            ric=raw,
            bloomberg=f"{base}:HK",
            isin="",
        )
    elif len(raw) > 3 and raw.endswith(".SI"):
        base = raw[:-3]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XSES",
            currency="SGD",
            country="SG",
            name=f"{base} (Singapore Exchange)",
            ric=raw,
            bloomberg=f"{base}:SP",
            isin="",
        )
    elif len(raw) > 3 and raw.endswith(".KS"):
        base = raw[:-3]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XKRX",
            currency="KRW",
            country="KR",
            name=f"{base} (Korea Exchange)",
            ric=raw,
            bloomberg=f"{base}:KS",
            isin="",
        )
    elif len(raw) > 3 and raw.endswith(".TW"):
        base = raw[:-3]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XTWS",
            currency="TWD",
            country="TW",
            name=f"{base} (Taiwan Stock Exchange)",
            ric=raw,
            bloomberg=f"{base}:TT",
            isin="",
        )
    elif len(raw) > 3 and raw.endswith(".PA"):
        base = raw[:-3]
        return SymbolInfo(
            canonical_id=raw,
            ticker=base,
            venue_mic="XPAR",
            currency="EUR",
            country="FR",
            name=f"{base} (Euronext Paris)",
            ric=raw,
            bloomberg=f"{base}:FP",
            isin="",
        )

    # 4-digit numeric code heuristics for Japan (e.g. 7203, 6758)
    if len(raw) == 4 and raw.isdigit():
        return SymbolInfo(
            canonical_id=f"{raw}.T",
            ticker=raw,
            venue_mic="XTKS",
            currency="JPY",
            country="JP",
            name=f"TSE Listed #{raw}",
            ric=f"{raw}.T",
            bloomberg=f"{raw}:JP",
            isin="",
        )

    # Fallback to default US market
    return SymbolInfo(
        canonical_id=raw,
        ticker=raw,
        venue_mic=default_venue,
        currency="USD",
        country="US",
        name=raw,
        ric=f"{raw}.O",
        bloomberg=f"{raw}:US",
        isin="",
    )


def get_native_currency(instrument_id: str) -> str:
    """Convenience helper to extract currency code from an instrument ID."""
    info = resolve_symbol(instrument_id)
    return info.currency
