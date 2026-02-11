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
    """
    if not species_list:
        click.echo("No CA native trees found in this area (that you haven't seen).")
        return

    table = [
        [s["common_name"] or s.get("db_common_name", ""), s["scientific_name"], s["count"]]
        for s in species_list
    ]
    click.echo(tabulate(table, headers=["Common Name", "Scientific Name", "Observations"], tablefmt="simple"))
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
