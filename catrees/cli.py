"""Click CLI entry points for catrees."""

import click

from catrees import db, inat, display


@click.group()
def cli():
    """California native tree finder."""
    pass


@cli.command()
@click.option("--search", default=None, help="Filter by name")
def species(search):
    """List CA native tree species from the local database."""
    if search:
        rows = db.search_species(search)
    else:
        rows = db.get_native_species()
    display.show_species_table(rows)


@cli.command("sync-taxa")
def sync_taxa():
    """Resolve iNaturalist taxon IDs for all CA native species."""
    db.ensure_taxon_id_column()

    species_rows = db.get_native_species()
    resolved = 0
    failed = []

    for sp in species_rows:
        sci_name = sp["scientific_name"]
        click.echo(f"  Resolving {sci_name}...", nl=False)
        result = inat.resolve_taxon(sci_name)
        if result:
            taxon_id, _, _ = result
            db.update_taxon_id(sp["id"], taxon_id)
            click.echo(f" taxon_id={taxon_id}")
            resolved += 1
        else:
            click.echo(" NOT FOUND")
            failed.append(sci_name)

    click.echo(f"\nResolved {resolved}/{len(species_rows)} species.")
    if failed:
        click.echo("Could not resolve:")
        for name in failed:
            click.echo(f"  - {name}")


def _resolve_location(from_place, lat, lng):
    """Resolve --from / --lat / --lng to a (lat, lng) tuple, or return None on error."""
    if from_place is not None:
        db.ensure_places_table()
        place = db.find_place(from_place)
        if place is None:
            click.echo(f"Place '{from_place}' not found. Use 'catrees places list' to see saved places.")
            return None
        return (place["lat"], place["lng"])
    elif lat is not None and lng is not None:
        return (lat, lng)
    else:
        click.echo("Provide either --from <place> or both --lat and --lng.")
        return None


@cli.command()
@click.option("--lat", default=None, type=float, help="Latitude")
@click.option("--lng", default=None, type=float, help="Longitude")
@click.option("--from", "from_place", default=None, help="Named place to search from")
@click.option("--radius", default=10, type=float, help="Search radius in km")
@click.option("--user", default=None, help="iNaturalist username to exclude already-seen species")
def nearby(lat, lng, from_place, radius, user):
    """Find CA native trees observed near a location."""
    coords = _resolve_location(from_place, lat, lng)
    if coords is None:
        return
    lat, lng = coords
    click.echo(f"Searching for native trees within {radius}km of ({lat}, {lng})...")

    # Get taxon IDs for our known CA native trees
    taxon_ids = db.get_native_taxon_ids()
    if not taxon_ids:
        click.echo("No taxon IDs found. Run 'catrees sync-taxa' first.")
        return

    # Get iNat observations filtered to our native tree taxa
    inat_species = inat.get_nearby_observations(lat, lng, radius, taxon_ids=taxon_ids)

    # Exclude already-observed species. Primary match is by iNat taxon_id to
    # handle synonyms (e.g. iNat returns 'Sambucus cerulea' but DB has
    # 'Sambucus mexicana' — same taxon_id, different name). Fall back to
    # normalized name matching for any DB species not yet sync'd with a taxon_id.
    if user:
        click.echo(f"Fetching life list for iNaturalist user '{user}'...")
        seen_taxon_ids = inat.get_user_life_list_taxon_ids(user)
        seen_normalized = set()
        seen_binomials = set()
    else:
        seen_taxon_ids = db.get_observed_taxon_ids()
        raw_seen = db.get_observed_scientific_names()
        seen_normalized = {" ".join(inat.normalize_name(n)) for n in raw_seen}
        seen_binomials = {" ".join(inat.normalize_name(n)[:2]) for n in raw_seen}

    def is_seen(sp):
        if sp.get("taxon_id") and sp["taxon_id"] in seen_taxon_ids:
            return True
        # Name fallback for DB species without a resolved taxon_id
        parts = inat.normalize_name(sp["scientific_name"])
        if " ".join(parts) in seen_normalized:
            return True
        if len(parts) > 2 and " ".join(parts[:2]) in seen_binomials:
            return True
        if len(parts) == 2 and " ".join(parts) in seen_binomials:
            return True
        return False

    inat_species = [s for s in inat_species if not is_seen(s)]

    display.show_nearby_results(inat_species)

    if not inat_species:
        return

    # Interactive target selection
    selection = click.prompt(
        "\nAdd to targets (e.g. 1,3,5 or 'none')", default="none"
    )
    if selection.strip().lower() == "none":
        return

    db.ensure_targets_tables()

    indices = []
    for part in selection.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part)
            if 1 <= idx <= len(inat_species):
                indices.append(idx - 1)

    added = 0
    for idx in indices:
        sp = inat_species[idx]
        was_added = db.add_target(
            sp["scientific_name"],
            sp["common_name"],
            sp["taxon_id"],
            sp.get("locations", []),
            search_lat=lat,
            search_lng=lng,
        )
        if was_added:
            click.echo(f"  Added: {sp['common_name'] or sp['scientific_name']}")
            added += 1
        else:
            click.echo(f"  Already targeted: {sp['common_name'] or sp['scientific_name']}")

    click.echo(f"{added} species added to targets.")


@cli.command()
@click.argument("name")
def find(name):
    """Find where a CA native tree species is observed in California."""
    # Look up in local DB first
    sp = db.find_species_by_name(name)
    if sp:
        click.echo(f"Found: {sp['common_name']} ({sp['scientific_name']})")
        search_name = sp["scientific_name"]
    else:
        click.echo(f"'{name}' not found in local database, searching iNaturalist directly...")
        search_name = name

    # Resolve to iNaturalist taxon
    result = inat.resolve_taxon(search_name)
    if not result:
        click.echo(f"Could not find '{search_name}' on iNaturalist.")
        return

    taxon_id, sci_name, common_name = result
    display_name = f"{common_name} ({sci_name})" if common_name else sci_name
    click.echo(f"Fetching observations for {display_name} in California...")

    # Get observations and cluster them
    observations = inat.get_species_observations_in_ca(taxon_id)
    clusters = inat.cluster_observations(observations)
    display.show_clusters(clusters, display_name)


@cli.command()
@click.argument("name")
@click.option("--lat", default=None, type=float, help="Latitude of reference point")
@click.option("--lng", default=None, type=float, help="Longitude of reference point")
@click.option("--from", "from_place", default=None, help="Named place to search from")
@click.option("--map", "map_path", default=None, type=click.Path(), help="Save an HTML map to this path")
@click.option("--trails", is_flag=True, default=False, help="Flag observations near hiking trails")
@click.option("--trail-radius", default=0.5, type=float, help="Max km to a trail to count as nearby (default: 0.5)")
@click.option("--limit", default=50, type=int, help="Max observations to show (default: 50)")
def nearest(name, lat, lng, from_place, map_path, trails, trail_radius, limit):
    """Find the closest observations of a species to a given point."""
    coords = _resolve_location(from_place, lat, lng)
    if coords is None:
        return
    lat, lng = coords
    result = inat.resolve_taxon(name)
    if not result:
        click.echo(f"Could not find '{name}' on iNaturalist.")
        return

    taxon_id, sci_name, common_name = result
    display_name = f"{common_name} ({sci_name})" if common_name else sci_name
    click.echo(f"Fetching observations for {display_name} in California...")

    observations = inat.get_species_observations_in_ca(taxon_id)
    if not observations:
        click.echo(f"No observations found for {display_name} in California.")
        return

    sorted_obs = sorted(
        ((inat.haversine_km(lat, lng, obs["lat"], obs["lng"]), obs) for obs in observations),
        key=lambda x: x[0],
    )
    top_obs = sorted_obs[:limit]

    trail_flags = None
    if trails:
        click.echo(f"Fetching hiking trails near top {len(top_obs)} observations...")
        lats = [obs["lat"] for _, obs in top_obs]
        lngs = [obs["lng"] for _, obs in top_obs]
        padding = 0.02  # ~2 km buffer
        try:
            trail_nodes = inat.get_trails_in_bbox(
                min(lats) - padding, min(lngs) - padding,
                max(lats) + padding, max(lngs) + padding,
            )
            click.echo(f"Found {len(trail_nodes)} trail nodes.")
            trail_flags = [
                inat.is_near_trail(obs["lat"], obs["lng"], trail_nodes, trail_radius)
                for _, obs in top_obs
            ]
        except Exception as e:
            click.echo(f"Could not fetch trail data: {e}")

    display.show_nearest(top_obs, lat, lng, trail_flags=trail_flags, trail_radius=trail_radius)

    if map_path:
        display.map_nearest(sorted_obs, lat, lng, display_name, map_path)


@cli.command("trail-obs")
@click.argument("name")
@click.option("--trail-radius", default=0.5, type=float, show_default=True, help="Max km from trail to count as nearby")
@click.option("--limit", default=50, type=int, show_default=True, help="Max species rows to display")
@click.option("--map", "map_path", default=None, type=click.Path(), help="Save an HTML map to this path")
def trail_obs(name, trail_radius, limit, map_path):
    """Show CA native trees observed near a named trail."""
    taxon_ids = db.get_native_taxon_ids()
    if not taxon_ids:
        click.echo("No taxon IDs found. Run 'catrees sync-taxa' first.")
        return

    click.echo(f"Looking up trail '{name}' in OpenStreetMap...")
    try:
        trail_nodes = inat.get_trail_by_name(name)
    except inat.TrailNotFoundError as e:
        click.echo(str(e))
        return
    except Exception as e:
        click.echo(f"Could not fetch trail data: {e}")
        return
    click.echo(f"Found {len(trail_nodes):,} trail nodes.")

    # Compute geographic span and warn if trail name is ambiguous
    lats = [n[0] for n in trail_nodes]
    lngs = [n[1] for n in trail_nodes]
    width_km = inat.haversine_km(min(lats), min(lngs), min(lats), max(lngs))
    height_km = inat.haversine_km(min(lats), min(lngs), max(lats), min(lngs))
    click.echo(f"Trail spans {width_km:.0f} km × {height_km:.0f} km.")
    if width_km > 500 or height_km > 500:
        click.echo(
            "Warning: Trail spans a very large area — results may include multiple "
            "unrelated trails sharing the same name."
        )

    min_lat, min_lng, max_lat, max_lng = inat.trail_bbox(trail_nodes, padding_km=trail_radius)
    click.echo(f"Fetching iNaturalist observations in bbox ({min_lat:.3f}, {min_lng:.3f}) — ({max_lat:.3f}, {max_lng:.3f})...")
    raw_obs = inat.get_observations_in_bbox(min_lat, min_lng, max_lat, max_lng, taxon_ids)
    click.echo(f"Filtering {len(raw_obs)} observations to within {trail_radius} km of trail...")

    # Filter to observations near the trail geometry
    near_obs = [
        obs for obs in raw_obs
        if inat.is_near_trail(obs["lat"], obs["lng"], trail_nodes, trail_radius)
    ]

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
            "lat": obs["lat"],
            "lng": obs["lng"],
            "observed_on": obs["observed_on"],
            "place_guess": obs["place_guess"],
            "uri": obs["uri"],
        })

    species_list = sorted(species_map.values(), key=lambda s: s["count"], reverse=True)[:limit]
    display.show_trail_obs(species_list, name, trail_radius, len(trail_nodes))

    if map_path:
        display.map_trail_obs(species_list, trail_nodes, name, map_path)


@cli.command()
@click.argument("name")
@click.option("--county", required=True, help="County name where observed")
@click.option("--date", "observed_on", default=None, help="Date observed (YYYY-MM-DD, defaults to today)")
def observe(name, county, observed_on):
    """Record a personal observation of a species."""
    sp = db.find_species_by_name(name)
    if not sp:
        click.echo(f"Species '{name}' not found in database.")
        click.echo("Use 'catrees species --search <term>' to find the correct name.")
        return

    county_id = db.find_county(county)
    if county_id is None:
        click.echo(f"County '{county}' not found in database.")
        return

    db.record_observation(sp["id"], county_id=county_id, observed_on=observed_on)
    date_str = f" on {observed_on}" if observed_on else ""
    click.echo(f"Recorded observation of {sp['common_name']} ({sp['scientific_name']}) in {county} County{date_str}")


@cli.group(invoke_without_command=True)
@click.option("--detail", is_flag=True, help="Show locations for each target")
@click.pass_context
def targets(ctx, detail):
    """View and manage target species."""
    if ctx.invoked_subcommand is None:
        db.ensure_targets_tables()
        all_targets = db.get_targets()
        display.show_targets(all_targets, detail=detail)


@targets.command("remove")
@click.argument("target_id", type=int)
def targets_remove(target_id):
    """Remove a target species by ID."""
    db.ensure_targets_tables()
    if db.remove_target(target_id):
        click.echo(f"Removed target {target_id}.")
    else:
        click.echo(f"Target {target_id} not found.")


@cli.group(invoke_without_command=True)
@click.pass_context
def places(ctx):
    """Manage saved locations."""
    if ctx.invoked_subcommand is None:
        db.ensure_places_table()
        display.show_places(db.get_places())


@places.command("list")
def places_list():
    """List all saved locations."""
    db.ensure_places_table()
    display.show_places(db.get_places())


@places.command("add")
@click.argument("name")
@click.option("--lat", required=True, type=float, help="Latitude")
@click.option("--lng", required=True, type=float, help="Longitude")
def places_add(name, lat, lng):
    """Save a named location."""
    db.ensure_places_table()
    if db.add_place(name, lat, lng):
        click.echo(f"Saved place '{name}' at ({lat}, {lng}).")
    else:
        click.echo(f"A place named '{name}' already exists.")


@places.command("remove")
@click.argument("place_id", type=int)
def places_remove(place_id):
    """Remove a saved location by ID."""
    db.ensure_places_table()
    if db.remove_place(place_id):
        click.echo(f"Removed place {place_id}.")
    else:
        click.echo(f"Place {place_id} not found.")


if __name__ == "__main__":
    cli()
