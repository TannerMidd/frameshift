#!/usr/bin/env python3
"""Guard the backend seams created by the server and journal decomposition.

This checker intentionally uses only the Python standard library so it can run
in a clean checkout before optional development tooling is installed.
"""

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Budget:
    path: str
    kind: str
    symbol: str | None
    maximum_lines: int
    guidance: str

    @property
    def label(self):
        suffix = f":{self.symbol}" if self.symbol else ""
        return f"{self.path}{suffix}"


@dataclass(frozen=True)
class Measurement:
    budget: Budget
    lines: int


class ArchitectureGuardError(Exception):
    """A source file cannot be measured according to its declared budget."""


BUDGETS = (
    Budget(
        "elite/server.py",
        "function",
        "create_app",
        1600,
        "Extract the next cohesive route group instead of extending create_app.",
    ),
    Budget(
        "elite/journal.py",
        "class",
        "JournalWatcher",
        2400,
        "Move another history, reducer, or lifecycle collaborator out of JournalWatcher.",
    ),
    Budget(
        "elite/system_routes.py",
        "module",
        None,
        360,
        "Split a cohesive admin domain into another route registrar.",
    ),
    Budget(
        "elite/journal_rebuild.py",
        "module",
        None,
        700,
        "Split a rebuild phase into a focused collaborator.",
    ),
)


def source_line_count(source):
    """Count physical source lines without charging for a final newline."""
    return len(source.splitlines()) if source else 0


def definition_span(source, kind, symbol, *, filename="<source>"):
    """Return the physical line span of one top-level class or function."""
    node_type = {"class": ast.ClassDef, "function": ast.FunctionDef}.get(kind)
    if node_type is None:
        raise ArchitectureGuardError(f"unsupported definition kind: {kind!r}")
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise ArchitectureGuardError(
            f"{filename} is not valid Python: {exc.msg} at line {exc.lineno}"
        ) from exc
    matches = [
        node
        for node in tree.body
        if isinstance(node, node_type) and node.name == symbol
    ]
    if len(matches) != 1:
        raise ArchitectureGuardError(
            f"{filename} must define exactly one top-level {kind} {symbol!r}; "
            f"found {len(matches)}"
        )
    node = matches[0]
    if node.end_lineno is None:
        raise ArchitectureGuardError(
            f"the Python parser did not report an end line for {filename}:{symbol}"
        )
    return node.end_lineno - node.lineno + 1


def measure_source(source, budget):
    """Measure source text using a module or named-definition budget."""
    if budget.kind == "module":
        lines = source_line_count(source)
    else:
        lines = definition_span(
            source, budget.kind, budget.symbol, filename=budget.path
        )
    return Measurement(budget, lines)


def limit_failure(measurement):
    """Return a clear failure for an over-budget measurement, if any."""
    budget = measurement.budget
    if measurement.lines <= budget.maximum_lines:
        return None
    return (
        f"{budget.label} is {measurement.lines} lines; hard ceiling "
        f"{budget.maximum_lines}. {budget.guidance}"
    )


def check_budgets(root=PROJECT_ROOT, budgets=BUDGETS):
    """Measure all budgets and return (measurements, failures)."""
    root = Path(root)
    measurements = []
    failures = []
    for budget in budgets:
        path = root / budget.path
        try:
            source = path.read_text(encoding="utf-8")
            measurement = measure_source(source, budget)
        except (ArchitectureGuardError, OSError, UnicodeError) as exc:
            failures.append(f"{budget.label} could not be measured: {exc}")
            continue
        measurements.append(measurement)
        failure = limit_failure(measurement)
        if failure:
            failures.append(failure)
    return measurements, failures


def run_self_test():
    """Exercise span lookup, module counting, and over-budget reporting."""
    function_budget = Budget("sample.py", "function", "build", 3, "Extract it.")
    source = "def build():\n    value = 1\n    return value\n\ndef other():\n    pass\n"
    measurement = measure_source(source, function_budget)
    assert measurement.lines == 3
    assert limit_failure(measurement) is None

    module_budget = Budget("large.py", "module", None, 2, "Split it.")
    measurement = measure_source("one\ntwo\nthree\n", module_budget)
    assert measurement.lines == 3
    assert limit_failure(measurement) == (
        "large.py is 3 lines; hard ceiling 2. Split it."
    )

    try:
        definition_span("class Different:\n    pass\n", "class", "Expected")
    except ArchitectureGuardError as exc:
        assert "found 0" in str(exc)
    else:
        raise AssertionError("missing definitions must fail closed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="test the checker logic instead of inspecting the repository",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        try:
            run_self_test()
        except (AssertionError, ArchitectureGuardError) as exc:
            print(f"Backend architecture guard self-test failed: {exc}", file=sys.stderr)
            return 1
        print("Backend architecture guard self-test OK.")
        return 0

    measurements, failures = check_budgets()
    if failures:
        print("Backend architecture guard failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("Backend architecture guard OK:")
    for measurement in measurements:
        budget = measurement.budget
        print(f"  - {budget.label}: {measurement.lines}/{budget.maximum_lines} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
