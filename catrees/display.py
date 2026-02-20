"""Output formatting for the catrees CLI."""

import click
from tabulate import tabulate


def show_species_table(species_rows):
    """Display a table of species from DB results (DictRow with id, scientific_name, common_name)."""
    if not species_rows:
        click.echo("No species found.")
        return

    table = [
        [row["id"], row["common_name"], row["scientific_name"]]
        for row in species_rows
    ]
    click.echo(tabulate(table, headers=["ID", "Common Name", "Scientific Name"], tablefmt="simple"))
    click.echo(f"\n{len(species_rows)} species")


def show_nearby_results(species_list):
    """Display nearby native tree species with observation counts.

    species_list: list of dicts with scientific_name, common_name, count, db_common_name
    Rows are numbered starting at 1 for interactive selection.
    """
    if not species_list:
        click.echo("No CA native trees found in this area (that you haven't seen).")
        return

    table = [
        [i, s["common_name"] or s.get("db_common_name", ""), s["scientific_name"], s["count"]]
        for i, s in enumerate(species_list, 1)
    ]
    click.echo(tabulate(table, headers=["#", "Common Name", "Scientific Name", "Observations"], tablefmt="simple"))
    click.echo(f"\n{len(species_list)} species")


def show_clusters(clusters, species_name):
    """Display location clusters for a species."""
    if not clusters:
        click.echo(f"No observations found for {species_name} in California.")
        return

    click.echo(f"\nTop locations for {species_name} in California:\n")
    table = []
    for i, c in enumerate(clusters[:20], 1):
        location = c["place_guess"] or f"{c['lat']:.2f}, {c['lng']:.2f}"
        table.append([i, location, c["count"], c["last_seen"]])

    click.echo(tabulate(table, headers=["#", "Location", "Observations", "Last Seen"], tablefmt="simple"))
    total = sum(c["count"] for c in clusters)
    click.echo(f"\n{total} total observations across {len(clusters)} locations")


def show_nearest(sorted_observations, from_lat, from_lng, limit=20):
    """Display observations sorted by distance from a given point."""
    if not sorted_observations:
        click.echo("No observations found.")
        return

    table = []
    for i, (dist, obs) in enumerate(sorted_observations[:limit], 1):
        table.append([
            i,
            f"{dist:.1f}",
            obs.get("place_guess", ""),
            f"{obs['lat']:.4f}",
            f"{obs['lng']:.4f}",
            obs.get("observed_on", ""),
            f"https://maps.google.com/?q={obs['lat']},{obs['lng']}",
        ])

    click.echo(tabulate(
        table,
        headers=["#", "Distance (km)", "Place", "Lat", "Lng", "Observed On", "Map"],
        tablefmt="simple",
    ))
    click.echo(f"\nShowing {min(limit, len(sorted_observations))} of {len(sorted_observations)} observations")


def map_nearest(sorted_observations, from_lat, from_lng, species_name, path):
    """Generate a folium HTML map of nearest observations."""
    import folium

    m = folium.Map(location=[from_lat, from_lng], zoom_start=9)

    # Reference point marker
    folium.Marker(
        [from_lat, from_lng],
        popup="You",
        icon=folium.Icon(color="red", icon="home", prefix="fa"),
    ).add_to(m)

    # Observation markers
    for i, (dist, obs) in enumerate(sorted_observations, 1):
        popup_text = (
            f"#{i} — {dist:.1f} km<br>"
            f"{obs.get('place_guess', '')}<br>"
            f"{obs.get('observed_on', '')}"
        )
        folium.Marker(
            [obs["lat"], obs["lng"]],
            popup=popup_text,
            icon=folium.Icon(color="green", icon="tree", prefix="fa"),
        ).add_to(m)

    m.save(path)
    click.echo(f"Map saved to {path}")


def show_targets(targets, detail=False):
    """Display the targets list.

    If detail is True, show individual locations for each target.
    """
    if not targets:
        click.echo("No targets saved. Use 'catrees nearby' to find and add species.")
        return

    if not detail:
        table = [
            [t["id"], t["common_name"] or "", t["scientific_name"],
             f"{t['search_lat']}, {t['search_lng']}" if t.get("search_lat") else "",
             len(t["locations"])]
            for t in targets
        ]
        click.echo(tabulate(table, headers=["ID", "Common Name", "Scientific Name", "Search Location", "Locations"], tablefmt="simple"))
        click.echo(f"\n{len(targets)} targets")
    else:
        for t in targets:
            name = f"{t['common_name']} ({t['scientific_name']})" if t["common_name"] else t["scientific_name"]
            search = f" — searched near {t['search_lat']}, {t['search_lng']}" if t.get("search_lat") else ""
            click.echo(f"\n[{t['id']}] {name}{search}")
            if t["locations"]:
                loc_table = [
                    [loc["lat"], loc["lng"], loc.get("observed_on", ""), loc.get("place_guess", "")]
                    for loc in t["locations"]
                ]
                click.echo(tabulate(loc_table, headers=["Lat", "Lng", "Observed On", "Place"], tablefmt="simple"))
            else:
                click.echo("  No locations recorded.")
        click.echo(f"\n{len(targets)} targets")
