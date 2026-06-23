"""SQLAlchemy models for tables we own.

The pgRouting topology tables (``ways``, ``ways_vertices_pgr``) live in the
``routing`` schema produced by the ETL and are NOT modelled here — they are
external (design D10).
"""

from open_routing_service.models.db_base import Base
from open_routing_service.models.etl_runs import EtlRun
from open_routing_service.models.facilities import Facility
from open_routing_service.models.function_version import FunctionVersion

__all__ = ["Base", "EtlRun", "Facility", "FunctionVersion"]
