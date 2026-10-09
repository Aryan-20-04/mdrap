"""MDRAP package alias for partition module."""
try:
    from partition import (
        ShardConfig,
        SymbolPartitioner,
        ConsumerSession,
        ConsumerFanoutManager,
        TenantQuotaManager,
        ShardInstance,
        FleetCoordinator,
    )
except ImportError:
    from src.partition import (
        ShardConfig,
        SymbolPartitioner,
        ConsumerSession,
        ConsumerFanoutManager,
        TenantQuotaManager,
        ShardInstance,
        FleetCoordinator,
    )

__all__ = [
    "ShardConfig",
    "SymbolPartitioner",
    "ConsumerSession",
    "ConsumerFanoutManager",
    "TenantQuotaManager",
    "ShardInstance",
    "FleetCoordinator",
]
