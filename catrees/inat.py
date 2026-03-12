"""iNaturalist API wrapper using pyinaturalist."""

import json
import math
import re
import urllib.parse
import urllib.request
from collections import defaultdict

from pyinaturalist import get_observations, get_taxa

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

_RANK_MARKERS = re.compile(r'\b(ssp|subsp|var|f)\.\s*', re.IGNORECASE)


def normalize_name(name):
    """Normalize a scientific name for comparison.

    Strips rank markers and lowercases, returning a list of name tokens:
      'Prunus ilicifolia ssp. ilicifolia' -> ['prunus', 'ilicifolia', 'ilicifolia']
      'Prunus ilicifolia'                 -> ['prunus', 'ilicifolia']
    """
    return _RANK_MARKERS.sub('', name).lower().split()

CA_PLACE_ID = 14  # California

_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1, lng1, lat2, lng2):
    """Return the great-circle distance in km between two points."""
    lat1, lng1, lat2, lng2 = (math.radians(v) for v in (lat1, lng1, lat2, lng2))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def _parse_location(obs):
    """Extract lat/lng/observed_on/place_guess from an observation dict.

    Returns a dict or None if location can't be parsed.
    """
    location = obs.get("location")
    if not location:
        return None
    if isinstance(location, (list, tuple)):
        if len(location) != 2:
            return None
        try:
            lat, lng = float(location[0]), float(location[1])
        except (ValueError, TypeError):
            return None
    else:
        parts = location.split(",")
        if len(parts) != 2:
            return None
        try:
            lat, lng = float(parts[0]), float(parts[1])
        except ValueError:
            return None
    observed_on = obs.get("observed_on", "")
    if hasattr(observed_on, "isoformat"):
        observed_on = observed_on.isoformat()[:10]
    return {
        "lat": lat,
        "lng": lng,
        "observed_on": str(observed_on) if observed_on else "",
        "place_guess": obs.get("place_guess", ""),
        "uri": obs.get("uri", ""),
    }


def get_nearby_observations(lat, lng, radius_km, taxon_ids=None, max_pages=3):
    """Fetch research-grade observations near a point.

    If taxon_ids is provided, filters to those taxa (instead of all Plantae).
    Paginates up to max_pages to get good coverage.

    Returns a list of dicts with taxon info and observation counts.
    """
    species_counts = defaultdict(
        lambda: {"count": 0, "taxon_id": None, "scientific_name": "",
                 "common_name": "", "locations": []}
    )

    for page in range(1, max_pages + 1):
        params = dict(
            lat=lat,
            lng=lng,
            radius=radius_km,
            quality_grade="research",
            per_page=200,
            page=page,
        )
        if taxon_ids:
            params["taxon_id"] = taxon_ids
        else:
            params["iconic_taxa"] = "Plantae"

        response = get_observations(**params)
        results = response.get("results", [])
        if not results:
            break

        for obs in results:
            taxon = obs.get("taxon")
            if not taxon:
                continue
            name = taxon.get("name", "").lower()
            if not name:
                continue
            entry = species_counts[name]
            entry["count"] += 1
            entry["taxon_id"] = taxon.get("id")
            entry["scientific_name"] = taxon.get("name", "")
            entry["common_name"] = taxon.get("preferred_common_name", "")

            # Collect observation location
            location = obs.get("location")
            if location:
                loc_dict = _parse_location(obs)
                if loc_dict:
                    entry["locations"].append(loc_dict)

        if len(results) < 200:
            break

    return sorted(species_counts.values(), key=lambda x: x["count"], reverse=True)


def get_user_life_list_taxon_ids(username):
    """Fetch the set of iNat taxon IDs a user has observed (research-grade)."""
    taxon_ids = set()
    page = 1
    while True:
        response = get_observations(
            user_login=username,
            quality_grade="research",
            per_page=200,
            page=page,
        )
        results = response.get("results", [])
        if not results:
            break
        for obs in results:
            taxon = obs.get("taxon")
            if taxon and taxon.get("id"):
                taxon_ids.add(taxon["id"])
        if len(results) < 200:
            break
        page += 1

    return taxon_ids


def resolve_taxon(name):
    """Resolve a species name to an iNaturalist taxon_id.

    Returns (taxon_id, scientific_name, common_name) or None.
    """
    response = get_taxa(q=name, rank=["species", "subspecies"])
    results = response.get("results", [])
    if not results:
        return None

    # Prefer an exact match on preferred_common_name or scientific name
    name_lower = name.lower()
    taxon = results[0]
    for r in results:
        if (r.get("preferred_common_name", "").lower() == name_lower
                or r.get("name", "").lower() == name_lower):
            taxon = r
            break

    return (
        taxon["id"],
        taxon.get("name", ""),
        taxon.get("preferred_common_name", ""),
    )


def get_species_observations_in_ca(taxon_id, max_pages=5):
    """Fetch research-grade observations of a taxon in California.

    Returns a list of dicts with lat, lng, observed_on, place_guess.
    """
    observations = []
    for page in range(1, max_pages + 1):
        response = get_observations(
            taxon_id=taxon_id,
            place_id=CA_PLACE_ID,
            quality_grade="research",
            per_page=200,
            page=page,
        )
        results = response.get("results", [])
        if not results:
            break
        for obs in results:
            loc_dict = _parse_location(obs)
            if loc_dict:
                observations.append(loc_dict)
        if len(results) < 200:
            break

    return observations


class TrailNotFoundError(Exception):
    def __init__(self, trail_name):
        self.trail_name = trail_name
        super().__init__(f"Trail not found in OSM: {trail_name!r}")


def get_trail_by_name(trail_name, timeout=60):
    """Fetch geometry nodes for a named trail in California from Overpass.

    Queries both OSM way and relation elements tagged as hiking trails.
    Returns a list of (lat, lng) tuples. Raises TrailNotFoundError if not found.
    """
    safe_name = trail_name.replace("\\", "\\\\").replace('"', '\\"')
    query = (
        f'[out:json][timeout:{timeout}];'
        f'area["ISO3166-2"="US-CA"]->.ca;'
        f'('
        f'way["highway"~"^(path|footway|track)$"]["name"="{safe_name}"](area.ca);'
        f'relation["type"="route"]["name"="{safe_name}"](area.ca);'
        f');'
        f'out geom;'
    )
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=data)
    with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
        result = json.loads(resp.read())

    nodes = set()
    for element in result.get("elements", []):
        if element.get("type") == "way":
            for node in element.get("geometry", []):
                nodes.add((node["lat"], node["lon"]))
        elif element.get("type") == "relation":
            for member in element.get("members", []):
                if member.get("type") == "way":
                    for node in member.get("geometry", []):
                        nodes.add((node["lat"], node["lon"]))

    if not nodes:
        raise TrailNotFoundError(trail_name)

    return list(nodes)


def trail_bbox(trail_nodes, padding_km=0.5):
    """Compute a bounding box around trail nodes with padding.

    Returns (min_lat, min_lng, max_lat, max_lng).
    Padding: 1 km ≈ 0.009 degrees.
    """
    padding_deg = padding_km * 0.009
    lats = [n[0] for n in trail_nodes]
    lngs = [n[1] for n in trail_nodes]
    return (
        min(lats) - padding_deg,
        min(lngs) - padding_deg,
        max(lats) + padding_deg,
        max(lngs) + padding_deg,
    )


def get_observations_in_bbox(min_lat, min_lng, max_lat, max_lng, taxon_ids, max_pages=10):
    """Fetch research-grade observations within a bounding box.

    Returns a list of dicts with keys: lat, lng, observed_on, place_guess,
    uri, taxon_id, scientific_name, common_name.
    """
    observations = []
    for page in range(1, max_pages + 1):
        response = get_observations(
            swlat=min_lat,
            swlng=min_lng,
            nelat=max_lat,
            nelng=max_lng,
            taxon_id=taxon_ids,
            quality_grade="research",
            per_page=200,
            page=page,
        )
        results = response.get("results", [])
        if not results:
            break
        for obs in results:
            loc = _parse_location(obs)
            if not loc:
                continue
            taxon = obs.get("taxon") or {}
            observations.append({
                "lat": loc["lat"],
                "lng": loc["lng"],
                "observed_on": loc["observed_on"],
                "place_guess": loc["place_guess"],
                "uri": loc["uri"],
                "taxon_id": taxon.get("id"),
                "scientific_name": taxon.get("name", ""),
                "common_name": taxon.get("preferred_common_name", ""),
            })
        if len(results) < 200:
            break
    return observations


def get_trails_in_bbox(min_lat, min_lng, max_lat, max_lng, timeout=30):
    """Fetch hiking trail geometry nodes from OSM within a bounding box.

    Queries ways tagged as path, footway, or track.
    Returns a list of (lat, lng) tuples representing trail nodes.
    """
    bbox = f"{min_lat},{min_lng},{max_lat},{max_lng}"
    query = (
        f'[out:json][timeout:{timeout}];'
        f'(way["highway"~"^(path|footway|track)$"]({bbox}););'
        f'out geom;'
    )
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=data)
    with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
        result = json.loads(resp.read())

    nodes = []
    for element in result.get("elements", []):
        if element.get("type") == "way":
            for node in element.get("geometry", []):
                nodes.append((node["lat"], node["lon"]))
    return nodes


def is_near_trail(lat, lng, trail_nodes, threshold_km=0.5):
    """Return True if (lat, lng) is within threshold_km of any trail node."""
    return any(haversine_km(lat, lng, tn[0], tn[1]) <= threshold_km for tn in trail_nodes)


def cluster_observations(observations, grid_size=0.1):
    """Cluster observations into geographic grid cells.

    grid_size is in degrees (~0.1 degree ≈ 10km).
    Returns clusters sorted by observation count, each with:
      count, center_lat, center_lng, last_seen, place_guess
    """
    clusters = defaultdict(lambda: {
        "count": 0,
        "lat_sum": 0.0,
        "lng_sum": 0.0,
        "last_seen": "",
        "place_guess": "",
    })

    for obs in observations:
        grid_key = (
            round(obs["lat"] / grid_size) * grid_size,
            round(obs["lng"] / grid_size) * grid_size,
        )
        cluster = clusters[grid_key]
        cluster["count"] += 1
        cluster["lat_sum"] += obs["lat"]
        cluster["lng_sum"] += obs["lng"]
        if obs["observed_on"] > cluster["last_seen"]:
            cluster["last_seen"] = obs["observed_on"]
            cluster["place_guess"] = obs.get("place_guess", "")

    result = []
    for (grid_lat, grid_lng), cluster in clusters.items():
        n = cluster["count"]
        result.append({
            "count": n,
            "lat": cluster["lat_sum"] / n,
            "lng": cluster["lng_sum"] / n,
            "last_seen": cluster["last_seen"],
            "place_guess": cluster["place_guess"],
        })

    return sorted(result, key=lambda x: x["count"], reverse=True)
