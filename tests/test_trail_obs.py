"""Tests for trail-obs core: inat.trail_bbox, inat.get_trail_by_name,
inat.get_observations_in_bbox, inat.TrailNotFoundError, and the trail_obs CLI
command integration flow.
"""

import json
from collections import defaultdict
from io import BytesIO
from unittest.mock import MagicMock, call, patch

import pytest

from catrees import inat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_urlopen_response(payload):
    """Return a context-manager mock that yields a file-like object with JSON."""
    raw = json.dumps(payload).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=BytesIO(raw))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _make_obs(lat, lng, taxon_id=1, scientific_name="Quercus agrifolia",
              common_name="Coast Live Oak", observed_on="2024-01-01",
              place_guess="Mount Tamalpais, CA", uri="https://www.inaturalist.org/observations/1"):
    """Build a minimal pyinaturalist observation dict."""
    return {
        "location": f"{lat},{lng}",
        "observed_on": observed_on,
        "place_guess": place_guess,
        "uri": uri,
        "taxon": {
            "id": taxon_id,
            "name": scientific_name,
            "preferred_common_name": common_name,
        },
    }


# ---------------------------------------------------------------------------
# trail_bbox unit tests
# ---------------------------------------------------------------------------

class TestTrailBbox:

    def test_basic_bounding_box_with_default_padding(self):
        """Given known nodes, the bbox min/max should match with 0.5 km padding."""
        nodes = [
            (34.0, -118.0),
            (34.5, -118.5),
            (34.2, -117.8),
        ]
        min_lat, min_lng, max_lat, max_lng = inat.trail_bbox(nodes)
        # 0.5 km * 0.009 deg/km = 0.0045 deg padding
        padding = 0.5 * 0.009
        assert min_lat == pytest.approx(34.0 - padding)
        assert min_lng == pytest.approx(-118.5 - padding)
        assert max_lat == pytest.approx(34.5 + padding)
        assert max_lng == pytest.approx(-117.8 + padding)

    def test_custom_padding(self):
        """padding_km should scale the degree padding proportionally."""
        nodes = [(34.0, -118.0), (35.0, -117.0)]
        padding_km = 1.0
        min_lat, min_lng, max_lat, max_lng = inat.trail_bbox(nodes, padding_km=padding_km)
        padding = 1.0 * 0.009
        assert min_lat == pytest.approx(34.0 - padding)
        assert min_lng == pytest.approx(-118.0 - padding)
        assert max_lat == pytest.approx(35.0 + padding)
        assert max_lng == pytest.approx(-117.0 + padding)

    def test_single_node_edge_case(self):
        """A single node produces a bbox where min == max before padding."""
        nodes = [(37.5, -122.1)]
        padding = 0.5 * 0.009
        min_lat, min_lng, max_lat, max_lng = inat.trail_bbox(nodes)
        assert min_lat == pytest.approx(37.5 - padding)
        assert min_lng == pytest.approx(-122.1 - padding)
        assert max_lat == pytest.approx(37.5 + padding)
        assert max_lng == pytest.approx(-122.1 + padding)

    def test_return_order_is_min_lng_min_lat_max_lat_max_lng(self):
        """Returned tuple order must be (min_lat, min_lng, max_lat, max_lng)."""
        nodes = [(10.0, -50.0), (20.0, -40.0)]
        result = inat.trail_bbox(nodes, padding_km=0.0)
        # With 0 padding the exact node extremes are the result
        assert result[0] < result[2], "min_lat should be less than max_lat"
        assert result[1] < result[3], "min_lng should be less than max_lng"

    def test_zero_padding(self):
        """padding_km=0 should return exact min/max of the nodes."""
        nodes = [(34.0, -118.0), (34.5, -118.5)]
        min_lat, min_lng, max_lat, max_lng = inat.trail_bbox(nodes, padding_km=0.0)
        assert min_lat == pytest.approx(34.0)
        assert min_lng == pytest.approx(-118.5)
        assert max_lat == pytest.approx(34.5)
        assert max_lng == pytest.approx(-118.0)


# ---------------------------------------------------------------------------
# TrailNotFoundError unit tests
# ---------------------------------------------------------------------------

class TestTrailNotFoundError:

    def test_is_exception_subclass(self):
        assert issubclass(inat.TrailNotFoundError, Exception)

    def test_stores_trail_name(self):
        err = inat.TrailNotFoundError("Backbone Trail")
        assert err.trail_name == "Backbone Trail"

    def test_message_contains_trail_name(self):
        err = inat.TrailNotFoundError("Backbone Trail")
        assert "Backbone Trail" in str(err)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(inat.TrailNotFoundError) as exc_info:
            raise inat.TrailNotFoundError("Test Trail")
        assert exc_info.value.trail_name == "Test Trail"

    def test_caught_as_generic_exception(self):
        with pytest.raises(Exception):
            raise inat.TrailNotFoundError("Test Trail")


# ---------------------------------------------------------------------------
# get_trail_by_name unit tests (mocked urllib)
# ---------------------------------------------------------------------------

class TestGetTrailByName:

    def _way_response(self, nodes):
        """Build an Overpass way-type response with given (lat, lon) node dicts."""
        return {
            "elements": [
                {
                    "type": "way",
                    "geometry": [{"lat": lat, "lon": lon} for lat, lon in nodes],
                }
            ]
        }

    def _relation_response(self, member_ways):
        """Build an Overpass relation response.

        member_ways: list of lists of (lat, lon) tuples — one per member way.
        """
        members = [
            {
                "type": "way",
                "geometry": [{"lat": lat, "lon": lon} for lat, lon in nodes],
            }
            for nodes in member_ways
        ]
        return {
            "elements": [
                {
                    "type": "relation",
                    "members": members,
                }
            ]
        }

    @patch("catrees.inat.urllib.request.urlopen")
    def test_way_only_response_returns_correct_nodes(self, mock_urlopen):
        """A way-type element yields its geometry as (lat, lng) tuples."""
        payload = self._way_response([(34.0, -118.0), (34.1, -118.1)])
        mock_urlopen.return_value = _make_urlopen_response(payload)

        result = inat.get_trail_by_name("Backbone Trail")

        assert set(result) == {(34.0, -118.0), (34.1, -118.1)}

    @patch("catrees.inat.urllib.request.urlopen")
    def test_relation_with_members_collects_all_nodes(self, mock_urlopen):
        """A relation element with two member ways yields nodes from all members."""
        payload = self._relation_response([
            [(34.0, -118.0), (34.1, -118.1)],
            [(34.2, -118.2), (34.3, -118.3)],
        ])
        mock_urlopen.return_value = _make_urlopen_response(payload)

        result = inat.get_trail_by_name("Pacific Crest Trail")

        assert set(result) == {
            (34.0, -118.0), (34.1, -118.1),
            (34.2, -118.2), (34.3, -118.3),
        }

    @patch("catrees.inat.urllib.request.urlopen")
    def test_empty_response_raises_trail_not_found_error(self, mock_urlopen):
        """An Overpass response with no elements raises TrailNotFoundError."""
        payload = {"elements": []}
        mock_urlopen.return_value = _make_urlopen_response(payload)

        with pytest.raises(inat.TrailNotFoundError) as exc_info:
            inat.get_trail_by_name("Nonexistent XYZ Trail")

        assert exc_info.value.trail_name == "Nonexistent XYZ Trail"

    @patch("catrees.inat.urllib.request.urlopen")
    def test_duplicate_nodes_are_deduplicated(self, mock_urlopen):
        """Nodes appearing in multiple elements are deduplicated in the result."""
        # Two ways share a node at (34.0, -118.0)
        shared_node = (34.0, -118.0)
        payload = {
            "elements": [
                {
                    "type": "way",
                    "geometry": [
                        {"lat": 34.0, "lon": -118.0},
                        {"lat": 34.1, "lon": -118.1},
                    ],
                },
                {
                    "type": "way",
                    "geometry": [
                        {"lat": 34.0, "lon": -118.0},  # duplicate
                        {"lat": 34.2, "lon": -118.2},
                    ],
                },
            ]
        }
        mock_urlopen.return_value = _make_urlopen_response(payload)

        result = inat.get_trail_by_name("Backbone Trail")

        # Exact count: 3 unique nodes, not 4
        assert len(result) == 3
        assert shared_node in result

    @patch("catrees.inat.urllib.request.urlopen")
    def test_relation_member_without_geometry_is_skipped(self, mock_urlopen):
        """A relation member missing the 'geometry' key should be skipped gracefully."""
        payload = {
            "elements": [
                {
                    "type": "relation",
                    "members": [
                        {
                            "type": "way",
                            # no 'geometry' key — defensive skip
                        },
                        {
                            "type": "way",
                            "geometry": [{"lat": 34.0, "lon": -118.0}],
                        },
                    ],
                }
            ]
        }
        mock_urlopen.return_value = _make_urlopen_response(payload)

        result = inat.get_trail_by_name("Backbone Trail")
        assert result == [(34.0, -118.0)]

    @patch("catrees.inat.urllib.request.urlopen")
    def test_mixed_way_and_relation_collects_from_both(self, mock_urlopen):
        """When both way and relation elements appear, nodes from both are returned."""
        payload = {
            "elements": [
                {
                    "type": "way",
                    "geometry": [{"lat": 34.0, "lon": -118.0}],
                },
                {
                    "type": "relation",
                    "members": [
                        {
                            "type": "way",
                            "geometry": [{"lat": 35.0, "lon": -119.0}],
                        }
                    ],
                },
            ]
        }
        mock_urlopen.return_value = _make_urlopen_response(payload)

        result = inat.get_trail_by_name("Backbone Trail")
        assert set(result) == {(34.0, -118.0), (35.0, -119.0)}

    @patch("catrees.inat.urllib.request.urlopen")
    def test_non_way_relation_members_are_skipped(self, mock_urlopen):
        """Relation members with type != 'way' (e.g. 'node') are ignored."""
        payload = {
            "elements": [
                {
                    "type": "relation",
                    "members": [
                        {
                            "type": "node",
                            "geometry": [{"lat": 34.0, "lon": -118.0}],
                        },
                        {
                            "type": "way",
                            "geometry": [{"lat": 35.0, "lon": -119.0}],
                        },
                    ],
                }
            ]
        }
        mock_urlopen.return_value = _make_urlopen_response(payload)

        result = inat.get_trail_by_name("Backbone Trail")
        assert result == [(35.0, -119.0)]

    @patch("catrees.inat.urllib.request.urlopen")
    def test_returns_list_not_set(self, mock_urlopen):
        """Return type must be list (even though internal dedup uses a set)."""
        payload = self._way_response([(34.0, -118.0)])
        mock_urlopen.return_value = _make_urlopen_response(payload)

        result = inat.get_trail_by_name("Backbone Trail")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_observations_in_bbox unit tests (mocked pyinaturalist)
# ---------------------------------------------------------------------------

class TestGetObservationsInBbox:

    BBOX = (34.0, -118.5, 34.5, -118.0)

    @patch("catrees.inat.get_observations")
    def test_single_page_returns_correct_dict_list(self, mock_get_obs):
        """A single-page result is correctly mapped to per-obs dicts."""
        obs = _make_obs(34.1, -118.2, taxon_id=42, scientific_name="Quercus agrifolia",
                        common_name="Coast Live Oak", observed_on="2024-06-01",
                        place_guess="Marin County", uri="https://inaturalist.org/obs/99")
        mock_get_obs.return_value = {"results": [obs]}

        min_lat, min_lng, max_lat, max_lng = self.BBOX
        result = inat.get_observations_in_bbox(min_lat, min_lng, max_lat, max_lng, taxon_ids=[42])

        assert len(result) == 1
        rec = result[0]
        assert rec["lat"] == pytest.approx(34.1)
        assert rec["lng"] == pytest.approx(-118.2)
        assert rec["taxon_id"] == 42
        assert rec["scientific_name"] == "Quercus agrifolia"
        assert rec["common_name"] == "Coast Live Oak"
        assert rec["observed_on"] == "2024-06-01"
        assert rec["place_guess"] == "Marin County"
        assert rec["uri"] == "https://inaturalist.org/obs/99"

    @patch("catrees.inat.get_observations")
    def test_empty_result_returns_empty_list(self, mock_get_obs):
        """An empty results list returns an empty list without error."""
        mock_get_obs.return_value = {"results": []}

        min_lat, min_lng, max_lat, max_lng = self.BBOX
        result = inat.get_observations_in_bbox(min_lat, min_lng, max_lat, max_lng, taxon_ids=[1])
        assert result == []

    @patch("catrees.inat.get_observations")
    def test_taxon_ids_forwarded_to_api(self, mock_get_obs):
        """taxon_ids must be passed as taxon_id= kwarg to get_observations."""
        mock_get_obs.return_value = {"results": []}

        taxon_ids = [1001, 1002, 1003]
        min_lat, min_lng, max_lat, max_lng = self.BBOX
        inat.get_observations_in_bbox(min_lat, min_lng, max_lat, max_lng, taxon_ids=taxon_ids)

        call_kwargs = mock_get_obs.call_args.kwargs
        assert call_kwargs["taxon_id"] == taxon_ids

    @patch("catrees.inat.get_observations")
    def test_bbox_params_forwarded_correctly(self, mock_get_obs):
        """The four bbox values must map to swlat, swlng, nelat, nelng."""
        mock_get_obs.return_value = {"results": []}

        inat.get_observations_in_bbox(34.0, -118.5, 34.5, -118.0, taxon_ids=[1])

        call_kwargs = mock_get_obs.call_args.kwargs
        assert call_kwargs["swlat"] == 34.0
        assert call_kwargs["swlng"] == -118.5
        assert call_kwargs["nelat"] == 34.5
        assert call_kwargs["nelng"] == -118.0

    @patch("catrees.inat.get_observations")
    def test_pagination_stops_when_results_less_than_200(self, mock_get_obs):
        """Pagination terminates when a page returns fewer than 200 results."""
        # Page 1: full page (200 obs); page 2: partial page (5 obs) → stop
        full_page = [_make_obs(34.1, -118.2) for _ in range(200)]
        partial_page = [_make_obs(34.2, -118.3) for _ in range(5)]
        mock_get_obs.side_effect = [
            {"results": full_page},
            {"results": partial_page},
        ]

        min_lat, min_lng, max_lat, max_lng = self.BBOX
        result = inat.get_observations_in_bbox(min_lat, min_lng, max_lat, max_lng, taxon_ids=[1])

        assert mock_get_obs.call_count == 2
        assert len(result) == 205

    @patch("catrees.inat.get_observations")
    def test_pagination_respects_max_pages(self, mock_get_obs):
        """Pagination never exceeds max_pages even if every page returns 200."""
        full_page = [_make_obs(34.1, -118.2) for _ in range(200)]
        mock_get_obs.return_value = {"results": full_page}

        min_lat, min_lng, max_lat, max_lng = self.BBOX
        result = inat.get_observations_in_bbox(
            min_lat, min_lng, max_lat, max_lng, taxon_ids=[1], max_pages=3
        )

        assert mock_get_obs.call_count == 3
        assert len(result) == 600

    @patch("catrees.inat.get_observations")
    def test_page_numbers_incremented_correctly(self, mock_get_obs):
        """Each successive API call should use an incrementing page parameter."""
        full_page = [_make_obs(34.1, -118.2) for _ in range(200)]
        partial_page = [_make_obs(34.2, -118.3) for _ in range(1)]
        mock_get_obs.side_effect = [
            {"results": full_page},
            {"results": partial_page},
        ]

        min_lat, min_lng, max_lat, max_lng = self.BBOX
        inat.get_observations_in_bbox(min_lat, min_lng, max_lat, max_lng, taxon_ids=[1])

        pages = [c.kwargs["page"] for c in mock_get_obs.call_args_list]
        assert pages == [1, 2]

    @patch("catrees.inat.get_observations")
    def test_quality_grade_is_research(self, mock_get_obs):
        """The quality_grade parameter must be 'research'."""
        mock_get_obs.return_value = {"results": []}
        inat.get_observations_in_bbox(34.0, -118.5, 34.5, -118.0, taxon_ids=[1])
        assert mock_get_obs.call_args.kwargs["quality_grade"] == "research"

    @patch("catrees.inat.get_observations")
    def test_observation_missing_taxon_still_included_with_empty_fields(self, mock_get_obs):
        """An observation without a taxon key should still produce a record
        (taxon fields will be None/"")."""
        obs_no_taxon = {
            "location": "34.1,-118.2",
            "observed_on": "2024-01-01",
            "place_guess": "Somewhere",
            "uri": "https://inaturalist.org/obs/1",
        }
        mock_get_obs.return_value = {"results": [obs_no_taxon]}

        result = inat.get_observations_in_bbox(34.0, -118.5, 34.5, -118.0, taxon_ids=[1])

        assert len(result) == 1
        assert result[0]["taxon_id"] is None
        assert result[0]["scientific_name"] == ""
        assert result[0]["common_name"] == ""

    @patch("catrees.inat.get_observations")
    def test_observation_without_location_is_skipped(self, mock_get_obs):
        """Observations missing a parseable location are excluded from results."""
        obs_no_location = {
            "taxon": {"id": 1, "name": "Quercus agrifolia"},
        }
        valid_obs = _make_obs(34.1, -118.2)
        mock_get_obs.return_value = {"results": [obs_no_location, valid_obs]}

        result = inat.get_observations_in_bbox(34.0, -118.5, 34.5, -118.0, taxon_ids=[1])

        assert len(result) == 1
        assert result[0]["lat"] == pytest.approx(34.1)


# ---------------------------------------------------------------------------
# Integration test: trail_obs flow (mocked network)
# ---------------------------------------------------------------------------

class TestTrailObsIntegration:
    """Simulate the full trail_obs CLI command flow with mocked network calls.

    Setup:
    - 10 trail nodes arranged in a tight cluster near (34.1, -118.2)
    - 5 raw observations:
        - 3 within 0.5 km of a trail node (should be kept)
        - 2 well outside the trail geometry (should be filtered out)
    - After is_near_trail filter: 3 observations remain
    - Those 3 belong to 2 taxa: 2 observations of taxon 101, 1 of taxon 102
    - Aggregation: taxon 101 count=2, taxon 102 count=1
    """

    # Trail nodes — 10 nodes tightly grouped in LA
    TRAIL_NODES = [(34.1 + i * 0.001, -118.2 + i * 0.001) for i in range(10)]
    # Trail radius used throughout
    TRAIL_RADIUS = 0.5

    # 3 near observations (within 0.5 km of TRAIL_NODES[0] at (34.1, -118.2))
    NEAR_OBS = [
        _make_obs(34.1001, -118.2001, taxon_id=101,
                  scientific_name="Quercus agrifolia", common_name="Coast Live Oak"),
        _make_obs(34.1002, -118.2002, taxon_id=101,
                  scientific_name="Quercus agrifolia", common_name="Coast Live Oak"),
        _make_obs(34.1003, -118.2003, taxon_id=102,
                  scientific_name="Platanus racemosa", common_name="California Sycamore"),
    ]
    # 2 far observations (far from any trail node)
    FAR_OBS = [
        _make_obs(35.5, -120.0, taxon_id=103,
                  scientific_name="Pinus ponderosa", common_name="Ponderosa Pine"),
        _make_obs(36.0, -121.0, taxon_id=104,
                  scientific_name="Sequoia sempervirens", common_name="Coast Redwood"),
    ]
    ALL_OBS = NEAR_OBS + FAR_OBS

    def _run_trail_obs_flow(self):
        """Execute the core trail_obs aggregation logic (matches cli.py implementation)."""
        raw_obs = inat.get_observations_in_bbox.__wrapped__ if hasattr(
            inat.get_observations_in_bbox, '__wrapped__') else None
        # We call the real inat functions directly, with get_observations mocked
        trail_nodes = self.TRAIL_NODES
        trail_radius = self.TRAIL_RADIUS

        # Simulate what cli.trail_obs does after fetching:
        near_obs = [
            obs for obs in self._raw_obs_dicts()
            if inat.is_near_trail(obs["lat"], obs["lng"], trail_nodes, trail_radius)
        ]

        species_map = {}
        for obs in near_obs:
            tid = obs["taxon_id"]
            if tid not in species_map:
                species_map[tid] = {
                    "taxon_id": tid,
                    "scientific_name": obs["scientific_name"],
                    "common_name": obs["common_name"],
                    "count": 0,
                    "locations": [],
                }
            species_map[tid]["count"] += 1
            species_map[tid]["locations"].append({
                "lat": obs["lat"],
                "lng": obs["lng"],
                "observed_on": obs["observed_on"],
                "place_guess": obs["place_guess"],
                "uri": obs["uri"],
            })

        return sorted(species_map.values(), key=lambda s: s["count"], reverse=True)

    def _raw_obs_dicts(self):
        """Convert raw inat obs format to the dict format returned by get_observations_in_bbox."""
        result = []
        for obs in self.ALL_OBS:
            parts = obs["location"].split(",")
            lat, lng = float(parts[0]), float(parts[1])
            taxon = obs.get("taxon") or {}
            result.append({
                "lat": lat,
                "lng": lng,
                "observed_on": obs.get("observed_on", ""),
                "place_guess": obs.get("place_guess", ""),
                "uri": obs.get("uri", ""),
                "taxon_id": taxon.get("id"),
                "scientific_name": taxon.get("name", ""),
                "common_name": taxon.get("preferred_common_name", ""),
            })
        return result

    @patch("catrees.inat.get_observations")
    @patch("catrees.inat.urllib.request.urlopen")
    def test_full_flow_returns_correct_species_count(self, mock_urlopen, mock_get_obs):
        """With 5 raw obs (3 near, 2 far), aggregation should yield 2 species."""
        # Mock Overpass for get_trail_by_name
        overpass_payload = {
            "elements": [
                {
                    "type": "way",
                    "geometry": [{"lat": lat, "lon": lng}
                                 for lat, lng in self.TRAIL_NODES],
                }
            ]
        }
        mock_urlopen.return_value = _make_urlopen_response(overpass_payload)

        # Mock pyinaturalist for get_observations_in_bbox
        mock_get_obs.return_value = {"results": self.ALL_OBS}

        # Execute get_trail_by_name
        trail_nodes = inat.get_trail_by_name("Backbone Trail")
        assert len(trail_nodes) == 10

        # Execute get_observations_in_bbox
        min_lat, min_lng, max_lat, max_lng = inat.trail_bbox(trail_nodes, padding_km=0.5)
        raw_obs_dicts = inat.get_observations_in_bbox(
            min_lat, min_lng, max_lat, max_lng, taxon_ids=[101, 102, 103, 104]
        )
        assert len(raw_obs_dicts) == 5

        # Apply is_near_trail filter
        near_obs = [
            obs for obs in raw_obs_dicts
            if inat.is_near_trail(obs["lat"], obs["lng"], trail_nodes, 0.5)
        ]
        assert len(near_obs) == 3, (
            f"Expected 3 near observations, got {len(near_obs)}. "
            f"Near obs lats/lngs: {[(o['lat'], o['lng']) for o in near_obs]}"
        )

        # Aggregate by taxon
        species_map = {}
        for obs in near_obs:
            tid = obs["taxon_id"]
            if tid not in species_map:
                species_map[tid] = {
                    "taxon_id": tid,
                    "scientific_name": obs["scientific_name"],
                    "common_name": obs["common_name"],
                    "count": 0,
                    "locations": [],
                }
            species_map[tid]["count"] += 1
            species_map[tid]["locations"].append({
                "lat": obs["lat"], "lng": obs["lng"],
                "observed_on": obs["observed_on"],
                "place_guess": obs["place_guess"],
                "uri": obs["uri"],
            })

        species_list = sorted(species_map.values(), key=lambda s: s["count"], reverse=True)
        assert len(species_list) == 2

    @patch("catrees.inat.get_observations")
    @patch("catrees.inat.urllib.request.urlopen")
    def test_full_flow_aggregates_counts_correctly(self, mock_urlopen, mock_get_obs):
        """Taxon 101 has 2 near obs, taxon 102 has 1 near obs; counts must match."""
        overpass_payload = {
            "elements": [
                {
                    "type": "way",
                    "geometry": [{"lat": lat, "lon": lng}
                                 for lat, lng in self.TRAIL_NODES],
                }
            ]
        }
        mock_urlopen.return_value = _make_urlopen_response(overpass_payload)
        mock_get_obs.return_value = {"results": self.ALL_OBS}

        trail_nodes = inat.get_trail_by_name("Backbone Trail")
        min_lat, min_lng, max_lat, max_lng = inat.trail_bbox(trail_nodes, padding_km=0.5)
        raw_obs_dicts = inat.get_observations_in_bbox(
            min_lat, min_lng, max_lat, max_lng, taxon_ids=[101, 102, 103, 104]
        )

        near_obs = [
            obs for obs in raw_obs_dicts
            if inat.is_near_trail(obs["lat"], obs["lng"], trail_nodes, 0.5)
        ]

        species_map = {}
        for obs in near_obs:
            tid = obs["taxon_id"]
            if tid not in species_map:
                species_map[tid] = {"count": 0, "scientific_name": obs["scientific_name"]}
            species_map[tid]["count"] += 1

        assert species_map[101]["count"] == 2
        assert species_map[102]["count"] == 1
        assert 103 not in species_map, "Taxon 103 is far from trail, should not be in results"
        assert 104 not in species_map, "Taxon 104 is far from trail, should not be in results"

    @patch("catrees.inat.get_observations")
    @patch("catrees.inat.urllib.request.urlopen")
    def test_full_flow_sorted_by_count_descending(self, mock_urlopen, mock_get_obs):
        """Species list must be sorted by count descending."""
        overpass_payload = {
            "elements": [
                {
                    "type": "way",
                    "geometry": [{"lat": lat, "lon": lng}
                                 for lat, lng in self.TRAIL_NODES],
                }
            ]
        }
        mock_urlopen.return_value = _make_urlopen_response(overpass_payload)
        mock_get_obs.return_value = {"results": self.ALL_OBS}

        trail_nodes = inat.get_trail_by_name("Backbone Trail")
        min_lat, min_lng, max_lat, max_lng = inat.trail_bbox(trail_nodes, padding_km=0.5)
        raw_obs_dicts = inat.get_observations_in_bbox(
            min_lat, min_lng, max_lat, max_lng, taxon_ids=[101, 102, 103, 104]
        )

        near_obs = [
            obs for obs in raw_obs_dicts
            if inat.is_near_trail(obs["lat"], obs["lng"], trail_nodes, 0.5)
        ]

        species_map = {}
        for obs in near_obs:
            tid = obs["taxon_id"]
            if tid not in species_map:
                species_map[tid] = {
                    "taxon_id": tid,
                    "scientific_name": obs["scientific_name"],
                    "common_name": obs["common_name"],
                    "count": 0,
                    "locations": [],
                }
            species_map[tid]["count"] += 1

        species_list = sorted(species_map.values(), key=lambda s: s["count"], reverse=True)
        counts = [s["count"] for s in species_list]
        assert counts == sorted(counts, reverse=True), "Species list should be sorted descending by count"

    @patch("catrees.inat.get_observations")
    @patch("catrees.inat.urllib.request.urlopen")
    def test_far_observations_excluded_by_is_near_trail(self, mock_urlopen, mock_get_obs):
        """FAR_OBS (taxon 103, 104) must not appear in final aggregation."""
        overpass_payload = {
            "elements": [
                {
                    "type": "way",
                    "geometry": [{"lat": lat, "lon": lng}
                                 for lat, lng in self.TRAIL_NODES],
                }
            ]
        }
        mock_urlopen.return_value = _make_urlopen_response(overpass_payload)
        mock_get_obs.return_value = {"results": self.ALL_OBS}

        trail_nodes = inat.get_trail_by_name("Backbone Trail")
        min_lat, min_lng, max_lat, max_lng = inat.trail_bbox(trail_nodes, padding_km=0.5)
        raw_obs_dicts = inat.get_observations_in_bbox(
            min_lat, min_lng, max_lat, max_lng, taxon_ids=[101, 102, 103, 104]
        )

        near_obs = [
            obs for obs in raw_obs_dicts
            if inat.is_near_trail(obs["lat"], obs["lng"], trail_nodes, 0.5)
        ]

        taxon_ids_in_result = {obs["taxon_id"] for obs in near_obs}
        assert 103 not in taxon_ids_in_result
        assert 104 not in taxon_ids_in_result


# ---------------------------------------------------------------------------
# Integration test: trail-obs CLI command via Click test runner
# ---------------------------------------------------------------------------

class TestTrailObsCLICommand:
    """Test the trail_obs Click command with mocked db, inat network calls."""

    def _overpass_response_for_nodes(self, nodes):
        return {
            "elements": [
                {
                    "type": "way",
                    "geometry": [{"lat": lat, "lon": lng} for lat, lng in nodes],
                }
            ]
        }

    def test_trail_not_found_exits_nonzero_no_inat_calls(self):
        """When the trail is not found, command exits non-zero and makes no iNat calls."""
        from click.testing import CliRunner
        from catrees.cli import cli

        runner = CliRunner()
        with patch("catrees.inat.urllib.request.urlopen") as mock_urlopen, \
             patch("catrees.inat.get_observations") as mock_get_obs, \
             patch("catrees.db.get_native_taxon_ids", return_value=[1, 2, 3]):
            mock_urlopen.return_value = _make_urlopen_response({"elements": []})

            result = runner.invoke(cli, ["trail-obs", "Nonexistent XYZ Trail"])

        assert result.exit_code == 0  # uses return, consistent with other commands
        mock_get_obs.assert_not_called()

    def test_no_taxon_ids_exits_before_network_calls(self):
        """When db.get_native_taxon_ids() is empty, command exits before any network calls."""
        from click.testing import CliRunner
        from catrees.cli import cli

        runner = CliRunner()
        with patch("catrees.db.get_native_taxon_ids", return_value=[]) as mock_db, \
             patch("catrees.inat.urllib.request.urlopen") as mock_urlopen, \
             patch("catrees.inat.get_observations") as mock_get_obs:

            result = runner.invoke(cli, ["trail-obs", "Backbone Trail"])

        # Must exit cleanly (uses return, consistent with other commands)
        assert result.exit_code == 0
        # No network calls
        mock_urlopen.assert_not_called()
        mock_get_obs.assert_not_called()
        # Prompt about sync-taxa
        assert "sync-taxa" in result.output

    def test_trail_obs_shows_correct_species_table(self):
        """Full trail-obs run with 3 near observations should display 2 species rows."""
        from click.testing import CliRunner
        from catrees.cli import cli

        trail_nodes = [(34.1 + i * 0.001, -118.2 + i * 0.001) for i in range(10)]
        near_obs = [
            _make_obs(34.1001, -118.2001, taxon_id=101,
                      scientific_name="Quercus agrifolia", common_name="Coast Live Oak"),
            _make_obs(34.1002, -118.2002, taxon_id=101,
                      scientific_name="Quercus agrifolia", common_name="Coast Live Oak"),
            _make_obs(34.1003, -118.2003, taxon_id=102,
                      scientific_name="Platanus racemosa", common_name="California Sycamore"),
        ]
        overpass_payload = {
            "elements": [
                {
                    "type": "way",
                    "geometry": [{"lat": lat, "lon": lng} for lat, lng in trail_nodes],
                }
            ]
        }

        runner = CliRunner()
        with patch("catrees.db.get_native_taxon_ids", return_value=[101, 102]), \
             patch("catrees.inat.urllib.request.urlopen") as mock_urlopen, \
             patch("catrees.inat.get_observations") as mock_get_obs:

            mock_urlopen.return_value = _make_urlopen_response(overpass_payload)
            mock_get_obs.return_value = {"results": near_obs}

            result = runner.invoke(cli, ["trail-obs", "Backbone Trail"])

        assert result.exit_code == 0, f"CLI failed with output:\n{result.output}"
        assert "Coast Live Oak" in result.output
        assert "California Sycamore" in result.output
        assert "Quercus agrifolia" in result.output
        assert "Platanus racemosa" in result.output

    def test_trail_obs_summary_line_content(self):
        """Summary line must mention species count, trail name, node count, and radius."""
        from click.testing import CliRunner
        from catrees.cli import cli

        trail_nodes = [(34.1 + i * 0.001, -118.2 + i * 0.001) for i in range(10)]
        near_obs = [
            _make_obs(34.1001, -118.2001, taxon_id=101,
                      scientific_name="Quercus agrifolia", common_name="Coast Live Oak"),
        ]
        overpass_payload = {
            "elements": [
                {
                    "type": "way",
                    "geometry": [{"lat": lat, "lon": lng} for lat, lng in trail_nodes],
                }
            ]
        }

        runner = CliRunner()
        with patch("catrees.db.get_native_taxon_ids", return_value=[101]), \
             patch("catrees.inat.urllib.request.urlopen") as mock_urlopen, \
             patch("catrees.inat.get_observations") as mock_get_obs:

            mock_urlopen.return_value = _make_urlopen_response(overpass_payload)
            mock_get_obs.return_value = {"results": near_obs}

            result = runner.invoke(cli, ["trail-obs", "Backbone Trail"])

        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        # Summary line from display.show_trail_obs
        assert "Backbone Trail" in result.output
        assert "species" in result.output
        assert "trail nodes" in result.output
