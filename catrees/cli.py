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


@cli.command()
@click.option("--lat", required=True, type=float, help="Latitude")
@click.option("--lng", required=True, type=float, help="Longitude")
@click.option("--radius", default=10, type=float, help="Search radius in km")
@click.option("--user", default=None, help="iNaturalist username to exclude already-seen species")
def nearby(lat, lng, radius, user):
    """Find CA native trees observed near a location."""
    click.echo(f"Searching for native trees within {radius}km of ({lat}, {lng})...")

    # Get taxon IDs for our known CA native trees
    taxon_ids = db.get_native_taxon_ids()
    if not taxon_ids:
        click.echo("No taxon IDs found. Run 'catrees sync-taxa' first.")
        return

    # Get iNat observations filtered to our native tree taxa
    inat_species = inat.get_nearby_observations(lat, lng, radius, taxon_ids=taxon_ids)

    # Exclude already-observed species (also match subspecies to their parent binomial)
    if user:
        click.echo(f"Fetching life list for iNaturalist user '{user}'...")
        seen = inat.get_user_life_list(user)
    else:
        seen = db.get_observed_scientific_names()

    def is_seen(sci_name):
        name = sci_name.lower()
        if name in seen:
            return True
        # Check binomial (genus + epithet) for subspecies/variety matches
        parts = name.split()
        if len(parts) > 2:
            return " ".join(parts[:2]) in seen
        return False

    inat_species = [s for s in inat_species if not is_seen(s["scientific_name"])]

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
@click.option("--lat", required=True, type=float, help="Latitude of reference point")
@click.option("--lng", required=True, type=float, help="Longitude of reference point")
def nearest(name, lat, lng):
    """Find the closest observations of a species to a given point."""
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
    display.show_nearest(sorted_obs, lat, lng)


@cli.command()
@click.argument("name")
@click.option("--county", required=True, help="County name where observed")
def observe(name, county):
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

    db.record_observation(sp["id"], county_id=county_id)
    click.echo(f"Recorded observation of {sp['common_name']} ({sp['scientific_name']}) in {county} County")


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


if __name__ == "__main__":
    cli()
