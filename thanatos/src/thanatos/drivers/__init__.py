"""Driver implementations.

M0: every driver method raises ``NotImplementedError``. The intent is to
freeze the Protocol shape and the per-driver class boundaries before the
real runtime work in M1.
"""

from thanatos.drivers.adb import AdbDriver
from thanatos.drivers.base import (
    ActResult,
    AssertResult,
    Driver,
    DriverError,
    PreflightResult,
    SemanticTree,
)
from thanatos.drivers.http import HttpDriver
from thanatos.drivers.playwright import PlaywrightDriver

__all__ = [
    "ActResult",
    "AdbDriver",
    "AssertResult",
    "Driver",
    "DriverError",
    "HttpDriver",
    "PlaywrightDriver",
    "PreflightResult",
    "SemanticTree",
]
