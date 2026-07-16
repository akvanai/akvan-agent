"""Console-script entry point for Akvan Agent."""

from agent.ui.app import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
