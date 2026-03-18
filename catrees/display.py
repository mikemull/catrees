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


def show_nearest(sorted_observations, from_lat, from_lng, limit=60, trail_flags=None, trail_radius=0.5):
    """Display observations sorted by distance from a given point."""
    if not sorted_observations:
        click.echo("No observations found.")
        return

    table = []
    for i, (dist, obs) in enumerate(sorted_observations[:limit], 1):
        row = [
            i,
            f"{dist:.1f}",
            obs.get("place_guess", ""),
            f"{obs['lat']:.4f}",
            f"{obs['lng']:.4f}",
            obs.get("observed_on", ""),
            obs.get("uri", ""),
        ]
        if trail_flags is not None:
            row.append("*" if trail_flags[i - 1] else "")
        table.append(row)

    headers = ["#", "Distance (km)", "Place", "Lat", "Lng", "Observed On", "iNaturalist"]
    if trail_flags is not None:
        headers.append("Trail")

    click.echo(tabulate(table, headers=headers, tablefmt="simple"))
    click.echo(f"\nShowing {min(limit, len(sorted_observations))} of {len(sorted_observations)} observations")
    if trail_flags is not None:
        near_count = sum(trail_flags)
        click.echo(f"* = within {trail_radius} km of a hiking trail ({near_count} of {len(trail_flags)} observations)")


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


def web_nearest(sorted_observations, from_lat, from_lng, species_name, path,
                trail_flags=None, trail_radius=0.5):
    """Generate an HTML page with an observation table and embedded folium map."""
    import folium
    import html as html_mod

    m = folium.Map(location=[from_lat, from_lng], zoom_start=9)

    folium.Marker(
        [from_lat, from_lng],
        popup="You",
        icon=folium.Icon(color="red", icon="home", prefix="fa"),
    ).add_to(m)

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

    map_html = m._repr_html_()

    # Build table rows
    has_trails = trail_flags is not None
    rows = []
    for i, (dist, obs) in enumerate(sorted_observations, 1):
        place = html_mod.escape(obs.get("place_guess", ""))
        uri = obs.get("uri", "")
        date = obs.get("observed_on", "")
        trail_cell = ""
        if has_trails:
            trail_cell = f"<td>{'&#x2713;' if trail_flags[i - 1] else ''}</td>"
        rows.append(
            f"<tr>"
            f"<td>{i}</td>"
            f"<td>{dist:.1f}</td>"
            f"<td>{place}</td>"
            f"<td>{obs['lat']:.4f}</td>"
            f"<td>{obs['lng']:.4f}</td>"
            f"<td>{date}</td>"
            f"<td><a href='{uri}' target='_blank'>view</a></td>"
            f"{trail_cell}"
            f"</tr>"
        )

    trail_header = "<th>Trail</th>" if has_trails else ""
    trail_note = ""
    if has_trails:
        near_count = sum(trail_flags)
        trail_note = f"<p>&#x2713; = within {trail_radius} km of a hiking trail ({near_count} of {len(trail_flags)} observations)</p>"

    page_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Nearest {html_mod.escape(species_name)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 20px; color: #333; }}
  h1 {{ font-size: 1.4em; margin-bottom: 4px; }}
  .subtitle {{ color: #666; margin-bottom: 16px; }}
  .container {{ display: flex; gap: 20px; height: calc(100vh - 120px); }}
  .table-panel {{ flex: 1; overflow-y: auto; min-width: 0; }}
  .map-panel {{ flex: 1; min-width: 0; }}
  .map-panel iframe, .map-panel .folium-map {{ width: 100% !important; height: 100% !important; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.85em; }}
  th, td {{ text-align: left; padding: 5px 8px; border-bottom: 1px solid #e0e0e0; white-space: nowrap; }}
  th {{ position: sticky; top: 0; background: #f5f5f5; font-weight: 600; }}
  tr:hover {{ background: #f9f9f9; }}
  a {{ color: #2a7ae2; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .note {{ font-size: 0.85em; color: #666; margin-top: 8px; }}
</style>
</head>
<body>
<h1>Nearest {html_mod.escape(species_name)}</h1>
<p class="subtitle">from ({from_lat:.4f}, {from_lng:.4f}) &middot; {len(sorted_observations)} observations</p>
<div class="container">
  <div class="table-panel">
    <table>
      <thead><tr>
        <th>#</th><th>km</th><th>Place</th><th>Lat</th><th>Lng</th><th>Date</th><th>iNat</th>{trail_header}
      </tr></thead>
      <tbody>
        {"".join(rows)}
      </tbody>
    </table>
    {trail_note}
  </div>
  <div class="map-panel">
    {map_html}
  </div>
</div>
</body>
</html>"""

    with open(path, "w") as f:
        f.write(page_html)
    click.echo(f"Web page saved to {path}")


def show_trail_obs(species_list, trail_name, trail_radius, node_count):
    """Display CA native tree observations near a named trail.

    species_list: list of dicts with taxon_id, scientific_name, common_name, count
    """
    if not species_list:
        click.echo(f"No CA native tree observations found within {trail_radius} km of {trail_name}.")
        return

    click.echo(f"Observations of CA native trees within {trail_radius} km of {trail_name}:\n")
    table = [
        [i, s["common_name"] or "", s["scientific_name"], s["count"]]
        for i, s in enumerate(species_list, 1)
    ]
    click.echo(tabulate(table, headers=["#", "Common Name", "Scientific Name", "Observations"], tablefmt="simple"))
    click.echo(f"\n{len(species_list)} species found near {trail_name} ({node_count:,} trail nodes, {trail_radius} km radius)")


def map_trail_obs(species_list, trail_nodes, trail_name, path):
    """Generate a folium HTML map of observations near a trail."""
    import folium

    if trail_nodes:
        center_lat = sum(n[0] for n in trail_nodes) / len(trail_nodes)
        center_lng = sum(n[1] for n in trail_nodes) / len(trail_nodes)
    else:
        center_lat, center_lng = 37.0, -119.5  # California center fallback

    m = folium.Map(location=[center_lat, center_lng], zoom_start=9)

    # Trail nodes as small dots
    for lat, lng in trail_nodes:
        folium.CircleMarker(
            location=[lat, lng],
            radius=2,
            color="#8B4513",
            fill=True,
            fill_opacity=0.5,
            weight=1,
        ).add_to(m)

    # Observation markers grouped by species
    for sp in species_list:
        for loc in sp.get("locations", []):
            popup_text = (
                f"{sp['common_name'] or sp['scientific_name']}<br>"
                f"{sp['scientific_name']}<br>"
                f"{loc.get('observed_on', '')}<br>"
                f"{loc.get('place_guess', '')}<br>"
                f"<a href='{loc.get('uri', '')}' target='_blank'>View on iNat</a>"
            )
            folium.Marker(
                [loc["lat"], loc["lng"]],
                popup=popup_text,
                icon=folium.Icon(color="green", icon="tree", prefix="fa"),
            ).add_to(m)

    m.save(path)
    click.echo(f"Map saved to {path}")


def show_places(places):
    """Display saved places as a table."""
    if not places:
        click.echo("No places saved. Use 'catrees places add' to save a location.")
        return

    table = [
        [p["id"], p["name"], f"{p['lat']:.6f}", f"{p['lng']:.6f}"]
        for p in places
    ]
    click.echo(tabulate(table, headers=["ID", "Name", "Lat", "Lng"], tablefmt="simple"))
    click.echo(f"\n{len(places)} places")


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
