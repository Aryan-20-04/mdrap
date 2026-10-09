"""MDRAP package alias for historical_verifier module."""

__stability__ = "stable"

try:
    from historical_verifier import AuditReport, HistoricalVerifier
except ImportError:
    from src.historical_verifier import AuditReport, HistoricalVerifier

__all__ = ["AuditReport", "HistoricalVerifier"]
