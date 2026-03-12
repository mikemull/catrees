# TDD: Trail Observations Command

**Date:** 2026-03-11
**Feature:** `catrees trail-obs` — show all CA native tree observations near a named trail

---

## 1. Problem Statement

### What problem are we solving?

The existing `nearest` command answers: "Given a species, which observations are close to a trail?" This feature inverts that: "Given a trail, which CA native tree species have been observed near it?" across all species simultaneously.

This is a natural companion workflow. A user planning to hike the Backbone Trail wants to know what CA native trees they might encounter along the route — without needing to query species one at a time.

### Constraints

- No new runtime dependencies. The project uses only `click`, `pyinaturalist`, `psycopg2-binary`, `tabulate`, and the stdlib.
- Overpass API has rate limits and a 30-second default timeout. Queries for long trails (PCT spans hundreds of miles) must be scoped to California only.
- iNaturalist API: pyinaturalist handles per-request throttling; pagination discipline is required.
- Local CLI only — no server or background job infrastructure.

### Acceptance Criteria

1. `catrees trail-obs "Backbone Trail"` prints a table of CA native tree species observed within 0.5 km of that trail, sorted by observation count descending.
2. `catrees trail-obs "Pacific Crest Trail" --trail-radius 1.0` uses a 1.0 km proximity threshold.
3. `catrees trail-obs "Backbone Trail" --limit 20` caps output at 20 species rows.
4. `catrees trail-obs "Backbone Trail" --map trail.html` generates a folium HTML map with trail nodes and observation markers.
5. If the named trail is not found in OSM, the command prints a descriptive error and exits non-zero without making any iNat requests.
6. If `sync-taxa` has never been run, the command prints a clear prompt and exits before making any network calls.
7. The flag is `--trail-radius` (consistent with `nearest`) defaulting to 0.5 km.
8. The summary line reports species found, total observations, trail name, node count, and bbox.

---

## 2. Context and Prior Art

### Existing code relevant to this feature

**`inat.get_trails_in_bbox(min_lat, min_lng, max_lat, max_lng)`** — queries Overpass for ways tagged `highway=path|footway|track` within a bbox. Returns flat `list[(lat, lng)]`. The critical gap: does not filter by name. Returns every matching way in the box.

**`inat.is_near_trail(lat, lng, trail_nodes, threshold_km)`** — point-in-proximity check. Reusable as-is.

**`inat.get_nearby_observations(lat, lng, radius_km, taxon_ids, max_pages)`** — fetches research-grade iNat observations near a point, filtered to taxon IDs. Returns pre-aggregated species-level results. Cannot be reused directly here because the proximity filter must happen per-observation before aggregation.

**`db.get_native_taxon_ids()`** — returns `inat_taxon_id` values for all synced CA native species. Exact filter set needed.

**`inat.haversine_km`** — reusable as-is.

**`display.show_nearby_results`** — displays species with counts; shapes closely match what the new command needs.

### How `nearest --trails` works (for contrast)

```
1. Resolve species → taxon_id
2. Fetch all CA observations of that taxon
3. Sort by distance from user point, take top N
4. Derive bbox from top-N observations + padding
5. get_trails_in_bbox(bbox) → all trail nodes in bbox
6. is_near_trail(obs, trail_nodes) → True/False flag per observation
7. Display table with "*" for trail-adjacent rows
```

The new feature inverts this: start with the trail, derive the bbox, fetch iNat observations, then filter to observations near the trail geometry.

### Overpass trail-by-name query pattern

OSM ways carry a `name` tag. Short local trails are modeled as a single OSM `way`. Long-distance trails (PCT, JMT, Backbone Trail) are typically modeled as OSM `relation` objects aggregating many member ways. Both types must be queried:

```
[out:json][timeout:60];
area["ISO3166-1"="US"]["name"="California"]->.ca;
(
  way["highway"~"^(path|footway|track)$"]["name"="Backbone Trail"](area.ca);
  relation["route"="hiking"]["name"="Backbone Trail"](area.ca);
);
out geom;
```

The `area` filter scopes results to California, essential for trails that cross state lines. `out geom` returns inline geometry, which is what the existing `get_trails_in_bbox` already uses for ways.

### iNat bbox fetching

`get_nearby_observations` takes center point + radius (circular). iNat's API also supports bbox parameters (`swlat`, `swlng`, `nelat`, `nelng`). A bbox query is more appropriate for trail corridors (elongated, not circular). A new thin wrapper using the bbox parameters avoids conflating two different spatial semantics in the existing function.

---

## 3. Architecture and System Design

### Component overview

```
cli.py: trail_obs()
    │
    ├── [early exit] db.get_native_taxon_ids() — fail if empty
    │
    ├── inat.get_trail_by_name(trail_name)          [NEW]
    │       └── Overpass API (by name + CA area filter)
    │           Returns: list[(lat, lng)] or raises TrailNotFoundError
    │
    ├── inat.trail_bbox(trail_nodes, padding_km)    [NEW]
    │       Returns: (min_lat, min_lng, max_lat, max_lng)
    │
    ├── inat.get_observations_in_bbox(              [NEW]
    │       min_lat, min_lng, max_lat, max_lng,
    │       taxon_ids, max_pages)
    │       └── pyinaturalist get_observations(swlat/swlng/nelat/nelng)
    │           Returns: list[dict] — one dict per raw observation
    │
    ├── [filter] is_near_trail(obs_lat, obs_lng,    [existing, unchanged]
    │       trail_nodes, trail_radius)
    │
    ├── [aggregate inline in cli.py]
    │       group observations by taxon → species list with counts
    │
    └── display.show_trail_obs(species_list, ...)   [NEW]
        display.map_trail_obs(...)                  [NEW, Phase 2]
```

### Key design decisions

**Do not modify `get_trails_in_bbox`.** It has a stable bbox-in, nodes-out contract used by `nearest --trails`. The new function handles the different input type (name string) and must parse both `way` and `relation` element types.

**New `get_observations_in_bbox` rather than repurposing `get_nearby_observations`.** The existing function pre-aggregates by taxon because that's all `nearby` needs. The new flow needs raw per-observation records so the proximity filter can run before aggregation.

**Aggregation stays in `cli.py`.** The `inat.py` functions remain data-fetching concerns; grouping and counting is orchestration that belongs in the command handler.

**`TrailNotFoundError` exception.** Cleaner than a `None` return for the not-found case because the caller must take a fundamentally different code path. `resolve_taxon` returns `None` for not-found; that pattern works there because the caller checks one condition. Here, the exception clearly communicates "do not proceed."

---

## 4. Data Models and Storage

No database schema changes. No migrations. All existing tables are read-only for this feature.

### In-memory shapes

**Trail nodes** (from `get_trail_by_name`):
```python
list[tuple[float, float]]  # (lat, lng) — same as get_trails_in_bbox
```

**Per-observation records** (from `get_observations_in_bbox`):
```python
{
    "lat": float,
    "lng": float,
    "observed_on": str,       # "YYYY-MM-DD"
    "place_guess": str,
    "uri": str,
    "taxon_id": int,
    "scientific_name": str,
    "common_name": str,
}
```

**Aggregated species results** (after proximity filter and grouping):
```python
{
    "taxon_id": int,
    "scientific_name": str,
    "common_name": str,
    "count": int,
    "locations": [            # retained for --map
        {"lat": float, "lng": float, "observed_on": str,
         "place_guess": str, "uri": str},
    ],
}
```
Sorted by `count` descending. Shape is close to what `show_nearby_results` already accepts.

---

## 5. API Contracts

### `inat.get_trail_by_name(trail_name, timeout=60)`

Returns `list[tuple[float, float]]` — all geometry nodes from matching OSM ways and relation member ways in California.

Raises `TrailNotFoundError(trail_name)` if Overpass returns zero elements.

**Node extraction:**
- `type=way`: iterate `element["geometry"]` → `(node["lat"], node["lon"])`. Same as existing function.
- `type=relation`: iterate `element["members"]` where `member["type"] == "way"`, then iterate `member["geometry"]` → `(node["lat"], node["lon"])`. Skip members without a `geometry` key (defensive).
- Deduplicate coordinates after collecting (exact duplicate `(lat, lng)` pairs from way intersections).

---

### `inat.trail_bbox(trail_nodes, padding_km=0.5)`

Returns `(min_lat, min_lng, max_lat, max_lng)`.

Padding: `1 km ≈ 0.009 degrees` — acceptable approximation for bbox buffer purposes. CLI layer passes the user's `--trail-radius` value as `padding_km`.

---

### `inat.get_observations_in_bbox(min_lat, min_lng, max_lat, max_lng, taxon_ids, max_pages=10)`

Returns `list[dict]` — one dict per observation with keys: `lat, lng, observed_on, place_guess, uri, taxon_id, scientific_name, common_name`.

Uses pyinaturalist `get_observations(swlat=, swlng=, nelat=, nelng=, taxon_id=taxon_ids, quality_grade="research", per_page=200, page=page)`.

Paginates until `len(results) < 200` or `max_pages` reached — same pattern as all existing pagination in `inat.py`.

---

### `inat.TrailNotFoundError`

```python
class TrailNotFoundError(Exception):
    def __init__(self, trail_name):
        self.trail_name = trail_name
        super().__init__(f"Trail not found in OSM: {trail_name!r}")
```

---

### CLI command: `catrees trail-obs`

```
catrees trail-obs NAME [OPTIONS]

  Show CA native trees observed near a named trail.

Arguments:
  NAME    Trail name (e.g. "Backbone Trail", "Pacific Crest Trail")

Options:
  --trail-radius FLOAT   Max km from trail to count as nearby [default: 0.5]
  --limit INTEGER        Max species rows to display [default: 50]
  --map PATH             Save an HTML map to this path
  --help
```

No `--lat`/`--lng`/`--from` flags. The trail geometry is the spatial anchor; there is no user reference point.

---

### `display.show_trail_obs(species_list, trail_name, trail_radius, node_count)`

Output:
```
Observations of CA native trees within 0.5 km of Backbone Trail:

#   Common Name               Scientific Name           Observations
--  ------------------------  ------------------------  ------------
1   Coast Live Oak            Quercus agrifolia                   47
2   California Sycamore       Platanus racemosa                   23
...

18 species found near Backbone Trail (1,204 trail nodes, 0.5 km radius)
```

---

### `display.map_trail_obs(species_list, trail_nodes, trail_name, path)` (Phase 2)

Folium map. Trail nodes rendered as small `CircleMarker` dots (not a PolyLine — node order from relations is not guaranteed to be geographically sequential across member ways, and a connected PolyLine would produce a visually incorrect result). Observation markers show species name, date, place guess, iNat link in popup. No reference-point marker.

---

## 6. Migration and Rollout Strategy

Entirely additive. No existing code modified. No schema changes. No data migrations.

Pre-condition check: the command checks `db.get_native_taxon_ids()` before any network call and exits with a clear message if the list is empty.

Rollout is deploy-and-done for a local CLI.

---

## 7. Risks and Open Questions

**Risk 1 — Overpass timeouts for long trails.** The PCT query with `area` + `out geom` on the public Overpass API can be slow. Default timeout is 60 seconds. CLI should distinguish timeout from not-found in error output.

**Risk 2 — Trail name disambiguation.** Generic names ("Canyon Trail", "Ridge Trail") match many unrelated trails in OSM and return conflated geometry. Phase 3 mitigates this with a bbox-span warning. Geographic scoping (e.g. `--county`) is deferred.

**Risk 3 — Large bbox and iNat API volume.** A 200 km trail bbox could yield thousands of raw observations pre-filter. `max_pages=10` caps this at 2,000 observations, which is acceptable. Post-filter count will be substantially lower.

**Risk 4 — OSM relation geometry format.** The `geometry` key on relation members may be absent if the Overpass version does not support `out geom` on relations. Implementation must defensively skip members without geometry and should be tested against real Backbone Trail data (a relation in OSM) during development.

**Open question — PolyLine map rendering.** Phase 1 uses CircleMarker scatter for trail nodes. Phase 2 could upgrade to per-way PolyLine segments if `get_trail_by_name` is extended to return `list[list[tuple[float, float]]]` (list of ways, each a list of nodes) rather than a flat node list. This is a data-shape decision that should be made before Phase 2 begins, as it affects the Phase 1 function signature. **Recommendation: keep Phase 1 returning a flat list; Phase 2 can add a new function variant or an optional `return_ways=True` parameter.**

---

## 8. Testing Strategy

### Unit tests

**`inat.trail_bbox`:** Known node list → assert correct min/max with padding. Edge case: single node.

**`inat.get_trail_by_name` (mocked Overpass HTTP):**
- Way-only response → correct node list
- Relation-only response with two member ways → all nodes collected
- Empty response → `TrailNotFoundError` raised
- Mixed way + relation → nodes from both
- Duplicate nodes → deduplication reduces result

**`inat.get_observations_in_bbox` (mocked pyinaturalist):**
- Single page → correct per-obs dict list
- Multi-page pagination termination
- Empty result → empty list
- `taxon_ids` correctly forwarded

**`inat.is_near_trail` — no new tests required; unchanged.**

### Integration tests (mocked network)

- `trail-obs "Backbone Trail"` with 10 trail nodes mocked, 5 observations mocked (3 near, 2 not) → table shows correct species from the 3 near observations
- Trail not found → exits non-zero, no iNat calls made
- No taxon IDs in DB → exits early before any network calls

### Manual acceptance tests

| Command | Expected |
|---|---|
| `catrees trail-obs "Backbone Trail"` | Species table, plausible CA tree list |
| `catrees trail-obs "Pacific Crest Trail"` | Large result, no crash |
| `catrees trail-obs "Nonexistent XYZ Trail"` | Clear not-found error |
| `catrees trail-obs "Backbone Trail" --trail-radius 1.0` | More species than 0.5 |
| `catrees trail-obs "Backbone Trail" --map out.html` | Valid HTML map file |
| `catrees trail-obs "Backbone Trail" --limit 5` | Exactly 5 rows |

**Performance target:** Under 60 seconds end-to-end on a normal connection.

---

## 9. Implementation Phases

### Phase 1 — Core (medium complexity)

- `inat.TrailNotFoundError`
- `inat.get_trail_by_name(trail_name, timeout=60)`
- `inat.trail_bbox(trail_nodes, padding_km=0.5)`
- `inat.get_observations_in_bbox(min_lat, min_lng, max_lat, max_lng, taxon_ids, max_pages=10)`
- `cli.trail_obs` command with `NAME`, `--trail-radius`, `--limit`; aggregation logic inline
- `display.show_trail_obs(species_list, trail_name, trail_radius, node_count)`

Gate: unit tests pass; `catrees trail-obs "Backbone Trail"` returns a plausible species table.

### Phase 2 — Map output (small complexity)

- `display.map_trail_obs(species_list, trail_nodes, trail_name, path)`
- `--map PATH` flag wired into command

Depends on Phase 1. Can start immediately after Phase 1 is done.

### Phase 3 — Disambiguation warning (small complexity)

- Print geographic span (bbox width × height in km) after trail fetch
- Warn if span exceeds 500 km in any direction

Depends on Phase 1. Can develop in parallel with Phase 2.
