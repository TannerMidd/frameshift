"""Backend architecture budget logic and repository contract."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.check_backend_architecture import (  # noqa: E402
    BUDGETS,
    check_budgets,
    run_self_test,
)


def test_guard_self_test():
    run_self_test()


def test_repository_stays_within_backend_budgets():
    measurements, failures = check_budgets()
    assert not failures, "\n".join(failures)
    assert [measurement.budget for measurement in measurements] == list(BUDGETS)
    assert all(
        measurement.lines <= measurement.budget.maximum_lines
        for measurement in measurements
    )


test_guard_self_test()
test_repository_stays_within_backend_budgets()

print("backend architecture OK: AST spans and extracted-module ceilings enforced")
