"""
Stores package metadata used by the application and build system.
Exposes the installed version so the CLI can display it consistently.
Keeps package-level exports small and intentional.
"""


__all__ = ["__version__"]

__version__ = "0.1.0"
