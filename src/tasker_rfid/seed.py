"""Load SKUs and customers from CSV files in seeds/ into the database.

Usage:
    uv run tasker-seed              # uses seeds/ next to pyproject.toml
    uv run tasker-seed path/to/dir  # a different seeds directory

Idempotent: rows already in the database are UPDATED to match the CSV,
new rows are INSERTED, nothing is duplicated. Rows removed from a CSV are
NOT deleted from the database (containers may reference them). To retire
a SKU or customer, set its `active` column to false instead.

Every row in both files is checked before anything is written. If any
row is invalid, the script prints every problem it found and exits
without touching the database.
"""

import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# SPEC.md section 3: the values allowed in skus.family and skus.tag_class.
SKU_FAMILIES = {
    "cones",
    "cups",
    "powder",
    "bag_in_box",
    "tetra",
    "sauce",
    "accessories",
}
TAG_CLASSES = {"paper", "paper_long", "on_metal"}

SKU_COLUMNS = ["sku_id", "name", "family", "units_per_box", "tag_class", "active"]
CUSTOMER_COLUMNS = ["customer_id", "name", "active"]

TRUE_WORDS = {"true", "t", "yes", "y", "1"}
FALSE_WORDS = {"false", "f", "no", "n", "0"}


# ---------------------------------------------------------------------------
# Reading and checking the CSV files
# ---------------------------------------------------------------------------


def read_csv(path: Path, expected_columns: list[str]) -> list[dict]:
    """Read a CSV into a list of dicts. Stops if the header is wrong."""
    if not path.exists():
        sys.exit(f"ERROR: {path} not found.")

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        if header != expected_columns:
            sys.exit(
                f"ERROR: {path.name} has the wrong columns.\n"
                f"  expected: {','.join(expected_columns)}\n"
                f"  found:    {','.join(header)}"
            )
        rows = []
        for row in reader:
            # Strip whitespace from every cell so a stray space in a
            # spreadsheet does not become a different ID.
            rows.append({k: (v or "").strip() for k, v in row.items()})
    return rows


def parse_bool(value: str) -> bool | None:
    """Turn spreadsheet-style true/false words into a bool, or None if unrecognised."""
    word = value.lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    return None


def check_skus(rows: list[dict]) -> list[str]:
    """Return a list of problems. Empty list means all rows are good."""
    problems = []
    seen_ids = set()
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        where = f"skus.csv row {n}"

        if not row["sku_id"]:
            problems.append(f"{where}: sku_id is blank")
        elif row["sku_id"] in seen_ids:
            problems.append(f"{where}: duplicate sku_id '{row['sku_id']}'")
        seen_ids.add(row["sku_id"])

        if not row["name"]:
            problems.append(f"{where}: name is blank")

        if row["family"] not in SKU_FAMILIES:
            problems.append(
                f"{where}: family '{row['family']}' is not one of "
                f"{', '.join(sorted(SKU_FAMILIES))}"
            )

        if not row["units_per_box"].isdigit() or int(row["units_per_box"]) <= 0:
            problems.append(
                f"{where}: units_per_box '{row['units_per_box']}' must be a whole number above 0"
            )

        if row["tag_class"] and row["tag_class"] not in TAG_CLASSES:
            problems.append(
                f"{where}: tag_class '{row['tag_class']}' must be blank or one of "
                f"{', '.join(sorted(TAG_CLASSES))}"
            )

        if parse_bool(row["active"]) is None:
            problems.append(f"{where}: active '{row['active']}' must be true or false")

    return problems


def check_customers(rows: list[dict]) -> list[str]:
    """Return a list of problems. Empty list means all rows are good."""
    problems = []
    seen_ids = set()
    for n, row in enumerate(rows, start=2):
        where = f"customers.csv row {n}"

        if not row["customer_id"]:
            problems.append(f"{where}: customer_id is blank")
        elif row["customer_id"] in seen_ids:
            problems.append(f"{where}: duplicate customer_id '{row['customer_id']}'")
        seen_ids.add(row["customer_id"])

        if not row["name"]:
            problems.append(f"{where}: name is blank")

        if parse_bool(row["active"]) is None:
            problems.append(f"{where}: active '{row['active']}' must be true or false")

    return problems


# ---------------------------------------------------------------------------
# Writing to the database
# ---------------------------------------------------------------------------

# ON CONFLICT ... DO UPDATE is what makes this safe to run twice:
# an existing row is overwritten with the CSV's values, not duplicated.

UPSERT_SKU = text("""
    INSERT INTO skus (sku_id, name, family, units_per_box, tag_class, active)
    VALUES (:sku_id, :name, :family, :units_per_box, :tag_class, :active)
    ON CONFLICT (sku_id) DO UPDATE SET
        name          = EXCLUDED.name,
        family        = EXCLUDED.family,
        units_per_box = EXCLUDED.units_per_box,
        tag_class     = EXCLUDED.tag_class,
        active        = EXCLUDED.active
""")

UPSERT_CUSTOMER = text("""
    INSERT INTO customers (customer_id, name, active)
    VALUES (:customer_id, :name, :active)
    ON CONFLICT (customer_id) DO UPDATE SET
        name   = EXCLUDED.name,
        active = EXCLUDED.active
""")


def sku_params(row: dict) -> dict:
    return {
        "sku_id": row["sku_id"],
        "name": row["name"],
        "family": row["family"],
        "units_per_box": int(row["units_per_box"]),
        "tag_class": row["tag_class"] or None,  # blank cell -> NULL
        "active": parse_bool(row["active"]),
    }


def customer_params(row: dict) -> dict:
    return {
        "customer_id": row["customer_id"],
        "name": row["name"],
        "active": parse_bool(row["active"]),
    }


def main() -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        sys.exit("ERROR: DATABASE_URL is not set. Copy .env.example to .env and fill it in.")

    if len(sys.argv) > 1:
        seeds_dir = Path(sys.argv[1])
    else:
        seeds_dir = Path(__file__).resolve().parents[2] / "seeds"

    skus = read_csv(seeds_dir / "skus.csv", SKU_COLUMNS)
    customers = read_csv(seeds_dir / "customers.csv", CUSTOMER_COLUMNS)

    problems = check_skus(skus) + check_customers(customers)
    if problems:
        print("Nothing was written. Fix these and run again:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(database_url)
    # engine.begin() is one transaction: either every row goes in, or none do.
    with engine.begin() as conn:
        for row in skus:
            conn.execute(UPSERT_SKU, sku_params(row))
        for row in customers:
            conn.execute(UPSERT_CUSTOMER, customer_params(row))

        sku_total = conn.execute(text("SELECT count(*) FROM skus")).scalar_one()
        customer_total = conn.execute(text("SELECT count(*) FROM customers")).scalar_one()

    print(f"skus:      {len(skus)} rows loaded from {seeds_dir / 'skus.csv'}")
    print(f"customers: {len(customers)} rows loaded from {seeds_dir / 'customers.csv'}")
    print(f"database now holds {sku_total} skus and {customer_total} customers")


if __name__ == "__main__":
    main()
