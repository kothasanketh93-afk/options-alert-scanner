#!/usr/bin/env python3
"""
run_tests.py -- Convenience test runner for quant_intelligence.

Usage (from the project root, i.e. the directory *containing*
quant_intelligence/):

    python3 quant_intelligence/run_tests.py

This exists because `python -m unittest discover` needs `-t` pointed at
the directory *above* the package for the tests' relative imports
(`from .helpers import ...`, `from ..pattern.matcher import ...`) to
resolve. Running this script does that automatically regardless of
your current working directory.
"""

from __future__ import annotations

import os
import sys
import unittest


def main() -> int:
    package_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(package_dir)
    sys.path.insert(0, project_root)

    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=os.path.join(package_dir, "tests"),
        pattern="test_*.py",
        top_level_dir=project_root,
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
