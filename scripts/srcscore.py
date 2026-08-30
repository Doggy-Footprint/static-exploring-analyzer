#!/usr/bin/env python3
"""
srcscore - deterministic source credibility scorer (zero LLM tokens).

Takes a list of URLs, applies the domain tier as a base score, then adjusts it
with citation counts, publication date, peer-review status and engagement
signals fetched from free APIs. Emits 0-100 scores and a verdict
(PRIMARY / SUPPORT / SKIM / WEAK / DROP / BLOCKED).

Input: one URL per line. `URL | title` is accepted. A JSON array
([{"url": ..., "title": ...}] or ["https://..."]) is auto-detected.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from srcscore_core.util import *  # noqa: E402,F401,F403
from srcscore_core.identifiers import *  # noqa: E402,F401,F403
from srcscore_core.fetchers import *  # noqa: E402,F401,F403
from srcscore_core.policy import *  # noqa: E402,F401,F403
from srcscore_core.scoring import *  # noqa: E402,F401,F403
from srcscore_core.io_format import *  # noqa: E402,F401,F403
from srcscore_core.cli import main  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main())
