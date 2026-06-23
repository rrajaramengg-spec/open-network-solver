"""Speed-from-OSM-tags helper for the routing ETL.

Used in two places:
  1. ``02_cost_columns.sql`` references the same speed values in its CASE
     statement — the two are kept in sync by this module's constant table.
  2. ``test_speed_tags.py`` imports this module directly and unit-tests every
     highway class, the maxspeed override path, and fallback behaviour.

Design D4 + task 2.2 / 2.3:
  speed = COALESCE(maxspeed, highway_class_default)
  cost_time = length_m * 3.6 / speed   (seconds)

All speeds in km/h.  The floor of 1 km/h prevents division-by-zero in edge
cases where both the tag and the default would produce 0.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Highway-class defaults (matches mapconfig.xml tag IDs and 02_cost_columns.sql)
# ---------------------------------------------------------------------------

#: Maps osm2pgrouting ``tag_id`` to default speed in km/h.
SPEED_BY_TAG_ID: dict[int, float] = {
    101: 110.0,  # motorway
    102:  90.0,  # motorway_link
    103: 100.0,  # trunk
    104:  80.0,  # trunk_link
    105:  80.0,  # primary
    106:  60.0,  # primary_link
    107:  60.0,  # secondary
    108:  50.0,  # secondary_link
    109:  50.0,  # tertiary
    110:  40.0,  # tertiary_link
    111:  30.0,  # residential
    112:  20.0,  # living_street
    113:  30.0,  # unclassified
    114:  20.0,  # service
}

#: Maps highway class *name* to default speed in km/h (used when parsing OSM
#: XML nodes/ways where we have the tag value, not the osm2pgrouting tag_id).
SPEED_BY_HIGHWAY: dict[str, float] = {
    "motorway":       110.0,
    "motorway_link":   90.0,
    "trunk":          100.0,
    "trunk_link":      80.0,
    "primary":         80.0,
    "primary_link":    60.0,
    "secondary":       60.0,
    "secondary_link":  50.0,
    "tertiary":        50.0,
    "tertiary_link":   40.0,
    "residential":     30.0,
    "living_street":   20.0,
    "unclassified":    30.0,
    "service":         20.0,
}

_DEFAULT_SPEED: float = 30.0
_MIN_SPEED: float = 1.0  # floor to prevent division-by-zero


def speed_kmh_from_tag_id(
    tag_id: int,
    maxspeed: float | None = None,
) -> float:
    """Return the effective routing speed in km/h given an osm2pgrouting tag_id.

    Args:
        tag_id:   osm2pgrouting tag_id (101..114 from mapconfig.xml).
        maxspeed: Parsed maxspeed value in km/h from the OSM maxspeed tag, or
                  ``None`` if not present.  If positive, overrides the class
                  default.

    Returns:
        Speed in km/h, floored at 1 km/h.
    """
    if maxspeed is not None and maxspeed > 0:
        return max(maxspeed, _MIN_SPEED)
    default = SPEED_BY_TAG_ID.get(tag_id, _DEFAULT_SPEED)
    return max(default, _MIN_SPEED)


def speed_kmh_from_highway(
    highway: str,
    maxspeed: float | None = None,
) -> float:
    """Return the effective routing speed in km/h given an OSM highway class name.

    Args:
        highway:  OSM ``highway`` tag value (e.g. ``"residential"``).
        maxspeed: Parsed maxspeed value in km/h, or ``None`` if absent.

    Returns:
        Speed in km/h, floored at 1 km/h.
    """
    if maxspeed is not None and maxspeed > 0:
        return max(maxspeed, _MIN_SPEED)
    default = SPEED_BY_HIGHWAY.get(highway, _DEFAULT_SPEED)
    return max(default, _MIN_SPEED)


def cost_time_s(length_m: float, speed_kmh: float) -> float:
    """Return travel time in seconds given edge length and speed.

    Args:
        length_m:  Edge length in metres.
        speed_kmh: Speed in km/h (must be > 0).

    Returns:
        Travel time in seconds.
    """
    return length_m * 3.6 / max(speed_kmh, _MIN_SPEED)
