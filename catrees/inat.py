"""iNaturalist API wrapper using pyinaturalist."""

from collections import defaultdict

from pyinaturalist import get_observations, get_taxa

CA_PLACE_ID = 14  # California


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


def get_user_life_list(username):
    """Fetch the set of species (lowercase scientific names) a user has observed on iNaturalist."""
    species_names = set()
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
            if taxon and taxon.get("name"):
                species_names.add(taxon["name"].lower())
        if len(results) < 200:
            break
        page += 1

    return species_names


def resolve_taxon(name):
    """Resolve a species name to an iNaturalist taxon_id.

    Returns (taxon_id, scientific_name, common_name) or None.
    """
    response = get_taxa(q=name, rank=["species", "subspecies"])
    results = response.get("results", [])
    if not results:
        return None
    taxon = results[0]
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
