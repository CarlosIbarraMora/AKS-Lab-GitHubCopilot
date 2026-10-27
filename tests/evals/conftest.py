"""Expose the shared loopback fleet fixture to eval tests."""

from tests.integration.fleet import fleet

__all__ = ["fleet"]
