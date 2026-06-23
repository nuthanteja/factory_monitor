"""Re-export shim: cloud.notifications.console_provider → ConsoleProvider.

The canonical implementation lives at cloud.notifications.console.ConsoleProvider.
This module exists so that the phase-2a test suite (and future code) can import
by the name declared in the Task-18 interface contract without touching the
existing module layout.
"""
from cloud.notifications.console import ConsoleProvider

__all__ = ["ConsoleProvider"]
