"""Run deterministic golden evaluations against the in-process ASGI fleet."""

from __future__ import annotations

import pytest

from tests.evals.test_scenarios import SCENARIO_TEST_PATH


def run() -> int:
    """Run S1-S5 through pytest and return its process exit code."""

    return int(pytest.main(["-v", "-p", "no:cacheprovider", str(SCENARIO_TEST_PATH)]))


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
