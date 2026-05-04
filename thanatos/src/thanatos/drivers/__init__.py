"""Driver implementations (atomic-MCP era).

The Driver Protocol (``preflight`` / ``observe`` / ``capture_evidence``) is the
shared contract. Atomic operations (tap / type / wait / ...) are domain-
specific and live on each concrete driver.
"""

from thanatos.drivers.adb import AdbDriver
from thanatos.drivers.base import (
    ActResult,
    Driver,
    DriverError,
    Evidence,
    PreflightResult,
    SemanticTree,
)
from thanatos.drivers.http import HttpDriver
from thanatos.drivers.playwright import PlaywrightDriver

__all__ = [
    "ActResult",
    "AdbDriver",
    "Driver",
    "DriverError",
    "Evidence",
    "HttpDriver",
    "PlaywrightDriver",
    "PreflightResult",
    "SemanticTree",
]
