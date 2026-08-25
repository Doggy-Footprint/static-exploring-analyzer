"""Shared sys.path bootstrap so test files can `import srcscore as S` and
`import check_policy as CP` the same way scripts/check_policy.py does."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
