"""Unit tests for ``infra/etl/speed_tags.py``.

Tests every highway class, the maxspeed override path, missing-tags fallback,
and the cost_time_s helper.

Implements Phase 2 task 2.6: "Unit tests for the speed-from-tags helper
(every highway class, maxspeed override, missing-tags fallback)."
"""

from __future__ import annotations

import pytest

# speed_tags is in infra/etl/, which is added to sys.path by tests/conftest.py.
import speed_tags as st


# ---------------------------------------------------------------------------
# speed_kmh_from_tag_id — every highway class
# ---------------------------------------------------------------------------


class TestSpeedKmhFromTagId:
    """Verify the tag_id → default speed lookup matches mapconfig.xml."""

    @pytest.mark.parametrize(
        "tag_id, expected",
        [
            (101, 110.0),  # motorway
            (102,  90.0),  # motorway_link
            (103, 100.0),  # trunk
            (104,  80.0),  # trunk_link
            (105,  80.0),  # primary
            (106,  60.0),  # primary_link
            (107,  60.0),  # secondary
            (108,  50.0),  # secondary_link
            (109,  50.0),  # tertiary
            (110,  40.0),  # tertiary_link
            (111,  30.0),  # residential
            (112,  20.0),  # living_street
            (113,  30.0),  # unclassified
            (114,  20.0),  # service
        ],
    )
    def test_class_default(self, tag_id: int, expected: float) -> None:
        assert st.speed_kmh_from_tag_id(tag_id) == expected

    def test_unknown_tag_id_falls_back_to_default(self) -> None:
        assert st.speed_kmh_from_tag_id(999) == st._DEFAULT_SPEED

    def test_maxspeed_zero_ignored(self) -> None:
        """maxspeed=0 is treated as absent (falls through to class default)."""
        assert st.speed_kmh_from_tag_id(111, maxspeed=0.0) == 30.0

    def test_maxspeed_negative_ignored(self) -> None:
        """Negative maxspeed is invalid; class default should apply."""
        assert st.speed_kmh_from_tag_id(111, maxspeed=-10.0) == 30.0

    def test_maxspeed_override_higher_than_default(self) -> None:
        """A posted 120 km/h limit on a residential road overrides the 30 km/h default."""
        result = st.speed_kmh_from_tag_id(111, maxspeed=120.0)
        assert result == 120.0

    def test_maxspeed_override_lower_than_default(self) -> None:
        """Posted 10 km/h school zone on a trunk overrides the 100 km/h default."""
        result = st.speed_kmh_from_tag_id(103, maxspeed=10.0)
        assert result == 10.0

    def test_floor_at_one_kmh(self) -> None:
        """Even a 0 km/h default should floor at 1 km/h to prevent division-by-zero."""
        # Patch the dict to inject a 0-speed class.
        original = st.SPEED_BY_TAG_ID.get(999)
        try:
            st.SPEED_BY_TAG_ID[999] = 0.0
            result = st.speed_kmh_from_tag_id(999)
            assert result == st._MIN_SPEED
        finally:
            if original is None:
                st.SPEED_BY_TAG_ID.pop(999, None)
            else:
                st.SPEED_BY_TAG_ID[999] = original


# ---------------------------------------------------------------------------
# speed_kmh_from_highway — by name (used by facilities/OSM-XML parsing)
# ---------------------------------------------------------------------------


class TestSpeedKmhFromHighway:
    """Verify the highway-name → default speed lookup."""

    @pytest.mark.parametrize(
        "highway, expected",
        [
            ("motorway",       110.0),
            ("motorway_link",   90.0),
            ("trunk",          100.0),
            ("trunk_link",      80.0),
            ("primary",         80.0),
            ("primary_link",    60.0),
            ("secondary",       60.0),
            ("secondary_link",  50.0),
            ("tertiary",        50.0),
            ("tertiary_link",   40.0),
            ("residential",     30.0),
            ("living_street",   20.0),
            ("unclassified",    30.0),
            ("service",         20.0),
        ],
    )
    def test_class_default(self, highway: str, expected: float) -> None:
        assert st.speed_kmh_from_highway(highway) == expected

    def test_unknown_highway_class_fallback(self) -> None:
        assert st.speed_kmh_from_highway("track") == st._DEFAULT_SPEED

    def test_empty_highway_class_fallback(self) -> None:
        assert st.speed_kmh_from_highway("") == st._DEFAULT_SPEED

    def test_maxspeed_overrides_highway_default(self) -> None:
        assert st.speed_kmh_from_highway("residential", maxspeed=50.0) == 50.0

    def test_maxspeed_none_uses_highway_default(self) -> None:
        assert st.speed_kmh_from_highway("residential", maxspeed=None) == 30.0

    def test_maxspeed_zero_treated_as_absent(self) -> None:
        assert st.speed_kmh_from_highway("residential", maxspeed=0.0) == 30.0


# ---------------------------------------------------------------------------
# cost_time_s — travel time conversion
# ---------------------------------------------------------------------------


class TestCostTimeS:
    def test_basic_conversion(self) -> None:
        # 1000 m at 100 km/h = 36 s
        assert st.cost_time_s(1000.0, 100.0) == pytest.approx(36.0, rel=1e-6)

    def test_zero_length_returns_zero(self) -> None:
        assert st.cost_time_s(0.0, 50.0) == pytest.approx(0.0)

    def test_floor_prevents_division_by_zero(self) -> None:
        # speed_kmh = 0 should be floored to _MIN_SPEED (1.0 km/h).
        result = st.cost_time_s(100.0, 0.0)
        expected = 100.0 * 3.6 / st._MIN_SPEED
        assert result == pytest.approx(expected, rel=1e-6)

    def test_small_segment(self) -> None:
        # 50 m at 30 km/h = 50 * 3.6 / 30 = 6 s
        assert st.cost_time_s(50.0, 30.0) == pytest.approx(6.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Consistency between tag_id and highway-name lookups
# ---------------------------------------------------------------------------


class TestConsistency:
    """Both lookup tables must be in sync with mapconfig.xml values."""

    @pytest.mark.parametrize(
        "tag_id, highway",
        [
            (101, "motorway"),
            (102, "motorway_link"),
            (103, "trunk"),
            (104, "trunk_link"),
            (105, "primary"),
            (106, "primary_link"),
            (107, "secondary"),
            (108, "secondary_link"),
            (109, "tertiary"),
            (110, "tertiary_link"),
            (111, "residential"),
            (112, "living_street"),
            (113, "unclassified"),
            (114, "service"),
        ],
    )
    def test_tag_id_and_highway_name_agree(self, tag_id: int, highway: str) -> None:
        assert st.SPEED_BY_TAG_ID[tag_id] == st.SPEED_BY_HIGHWAY[highway]

    def test_speed_by_tag_id_and_highway_have_same_length(self) -> None:
        assert len(st.SPEED_BY_TAG_ID) == len(st.SPEED_BY_HIGHWAY)
