"""MDRAP Data Export Utility.

Exports canonical market data and quarantined events from SQLite storage into
Parquet (optional dependency `pyarrow`), JSON, or CSV.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from typing import Any


def export_data(
    db_path: str,
    table: str = "canonical_events",
    output_path: str = "export.parquet",
    fmt: str = "parquet",
    limit: int | None = None,
) -> int:
    """Export database table rows to file in specified format.

    Returns:
        Number of rows exported.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Parameterized verification against sqlite_master to strictly prevent SQL injection
    cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?", (table,))
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"Table or view '{table}' does not exist in database.")

    query = f'SELECT * FROM "{table}"'
    if limit is not None:
        query += f" LIMIT {int(limit)}"

    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return 0

    col_names = [col[0] for col in cursor.description]
    data_dicts = [dict(row) for row in rows]

    parent_dir = os.path.dirname(os.path.abspath(output_path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    fmt_lower = fmt.lower()

    if fmt_lower == "parquet":
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            raise ImportError(
                "Optional dependency 'pyarrow' is required for Parquet export. "
                "Install it with: pip install pyarrow (or use --format json / --format csv)."
            )

        # Convert dict of lists for PyArrow Table
        pydict = {col: [d[col] for d in data_dicts] for col in col_names}
        table_arrow = pa.Table.from_pydict(pydict)
        pq.write_table(table_arrow, output_path)

    elif fmt_lower == "json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data_dicts, f, indent=2)

    elif fmt_lower == "csv":
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=col_names)
            writer.writeheader()
            writer.writerows(data_dicts)
    else:
        raise ValueError(f"Unsupported export format '{fmt}'. Choose from: parquet, json, csv.")

    return len(data_dicts)
