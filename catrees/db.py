"""Postgres connection and queries for the trees database."""

import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras


def get_connection():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is required")
    return psycopg2.connect(url)


@contextmanager
def get_cursor():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            yield cur
            conn.commit()
    finally:
        conn.close()


def get_native_species():
    """Return all CA native tree species."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, scientific_name, common_name "
            "FROM species WHERE ca_native = true "
            "ORDER BY common_name"
        )
        return cur.fetchall()


def search_species(term):
    """Search species by common or scientific name."""
    with get_cursor() as cur:
        pattern = f"%{term}%"
        cur.execute(
            "SELECT id, scientific_name, common_name "
            "FROM species WHERE ca_native = true "
            "AND (lower(common_name) LIKE lower(%s) "
            "     OR lower(scientific_name) LIKE lower(%s)) "
            "ORDER BY common_name",
            (pattern, pattern),
        )
        return cur.fetchall()


def find_species_by_name(name):
    """Find a single species by common or scientific name (exact-ish match)."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, scientific_name, common_name "
            "FROM species WHERE ca_native = true "
            "AND (lower(common_name) = lower(%s) "
            "     OR lower(scientific_name) = lower(%s))",
            (name, name),
        )
        row = cur.fetchone()
        if row:
            return row
        # Fall back to partial match
        pattern = f"%{name}%"
        cur.execute(
            "SELECT id, scientific_name, common_name "
            "FROM species WHERE ca_native = true "
            "AND (lower(common_name) LIKE lower(%s) "
            "     OR lower(scientific_name) LIKE lower(%s)) "
            "ORDER BY common_name LIMIT 1",
            (pattern, pattern),
        )
        return cur.fetchone()


def get_native_species_set():
    """Return a set of lowercase scientific names for all CA native trees."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT lower(scientific_name) FROM species WHERE ca_native = true"
        )
        return {row[0] for row in cur.fetchall()}


def get_observed_species_ids():
    """Return set of species IDs the user has observed locally."""
    with get_cursor() as cur:
        cur.execute("SELECT DISTINCT species_id FROM observations")
        return {row[0] for row in cur.fetchall()}


def get_observed_scientific_names():
    """Return set of lowercase scientific names the user has observed."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT lower(s.scientific_name) "
            "FROM observations o "
            "JOIN species s ON s.id = o.species_id"
        )
        return {row[0] for row in cur.fetchall()}


def find_county(name):
    """Find a county ID by name (case-insensitive partial match)."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id FROM counties WHERE lower(name) = lower(%s)",
            (name,),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "SELECT id FROM counties WHERE lower(name) LIKE lower(%s) LIMIT 1",
            (f"%{name}%",),
        )
        row = cur.fetchone()
        return row[0] if row else None


def record_observation(species_id, county_id=None):
    """Record a personal observation."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO observations (species_id, county_id) VALUES (%s, %s)",
            (species_id, county_id),
        )


def ensure_taxon_id_column():
    """Add inat_taxon_id column to species table if it doesn't exist."""
    with get_cursor() as cur:
        cur.execute(
            "ALTER TABLE species ADD COLUMN IF NOT EXISTS inat_taxon_id INTEGER"
        )


def update_taxon_id(species_id, taxon_id):
    """Set inat_taxon_id on a species row."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE species SET inat_taxon_id = %s WHERE id = %s",
            (taxon_id, species_id),
        )


def get_native_taxon_ids():
    """Return list of inat_taxon_id values (non-null) for CA native species."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT inat_taxon_id FROM species "
            "WHERE ca_native = true AND inat_taxon_id IS NOT NULL"
        )
        return [row[0] for row in cur.fetchall()]


def get_native_species_by_taxon_id():
    """Return dict mapping inat_taxon_id to species row."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, scientific_name, common_name, inat_taxon_id "
            "FROM species WHERE ca_native = true AND inat_taxon_id IS NOT NULL"
        )
        return {row["inat_taxon_id"]: row for row in cur.fetchall()}


def ensure_targets_tables():
    """Create targets and target_locations tables if they don't exist."""
    with get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id SERIAL PRIMARY KEY,
                scientific_name TEXT NOT NULL UNIQUE,
                common_name TEXT,
                inat_taxon_id INTEGER,
                added_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS target_locations (
                id SERIAL PRIMARY KEY,
                target_id INTEGER REFERENCES targets(id) ON DELETE CASCADE,
                lat DOUBLE PRECISION NOT NULL,
                lng DOUBLE PRECISION NOT NULL,
                observed_on TEXT,
                place_guess TEXT
            )
        """)


def add_target(scientific_name, common_name, inat_taxon_id, locations):
    """Insert a target species with its observation locations.

    Skips if the species is already in the targets table.
    Returns True if added, False if already existed.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT id FROM targets WHERE scientific_name = %s",
            (scientific_name,),
        )
        if cur.fetchone():
            return False
        cur.execute(
            "INSERT INTO targets (scientific_name, common_name, inat_taxon_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (scientific_name, common_name, inat_taxon_id),
        )
        target_id = cur.fetchone()[0]
        for loc in locations:
            cur.execute(
                "INSERT INTO target_locations (target_id, lat, lng, observed_on, place_guess) "
                "VALUES (%s, %s, %s, %s, %s)",
                (target_id, loc["lat"], loc["lng"],
                 loc.get("observed_on", ""), loc.get("place_guess", "")),
            )
        return True


def get_targets():
    """Return all targets with their locations.

    Returns a list of dicts, each with target fields and a 'locations' list.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, scientific_name, common_name, inat_taxon_id, added_at "
            "FROM targets ORDER BY added_at"
        )
        targets = []
        for row in cur.fetchall():
            target = dict(row)
            cur.execute(
                "SELECT lat, lng, observed_on, place_guess "
                "FROM target_locations WHERE target_id = %s",
                (row["id"],),
            )
            target["locations"] = [dict(loc) for loc in cur.fetchall()]
            targets.append(target)
        return targets


def remove_target(target_id):
    """Delete a target by ID (cascade removes its locations).

    Returns True if a row was deleted, False if not found.
    """
    with get_cursor() as cur:
        cur.execute("DELETE FROM targets WHERE id = %s", (target_id,))
        return cur.rowcount > 0
