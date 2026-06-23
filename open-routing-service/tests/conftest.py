"""Project-wide pytest fixtures.

Currently only adds the ETL orchestrator's source directory (``infra/etl``) to
``sys.path`` so unit tests can ``import load_osm`` directly. The ETL script
lives outside the installed Python package because it ships in a separate
container; this keeps the test entry point honest about that boundary.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root: open-network-solver/
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ETL_DIR = _REPO_ROOT / "infra" / "etl"

if str(_ETL_DIR) not in sys.path:
    sys.path.insert(0, str(_ETL_DIR))
