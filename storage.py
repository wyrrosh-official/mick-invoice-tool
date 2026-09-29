"""Local SQLite storage for confirmed invoices, used to build price history."""

import shutil
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from validation import parse_date

DB_PATH = Path(__file__).parent / "data" / "invoices.db"
BACKUPS_DIR = Path(__file__).parent / "data" / "backups"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoices (
            id TEXT PRIMARY KEY,
            supplier TEXT,
            invoice_number TEXT,
            invoice_date TEXT,
            department TEXT,
            gst REAL,
            invoice_total REAL,
            image_filename TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS line_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id TEXT NOT NULL REFERENCES invoices(id),
            description TEXT,
            qty REAL,
            unit TEXT,
            unit_price REAL,
            line_total REAL,
            check_flag INTEGER,
            check_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stocktakes (
            id TEXT PRIMARY KEY,
            count_date TEXT,
            source_images TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stocktake_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stocktake_id TEXT NOT NULL REFERENCES stocktakes(id),
            department TEXT,
            description TEXT,
            counted_qty REAL,
            unit TEXT,
            unit_price REAL,
            value REAL,
            check_flag INTEGER,
            check_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            kind TEXT,
            filename TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            success INTEGER
        )
        """
    )
    conn.commit()
    conn.close()


def log_api_call(
    kind: str, filename: str, input_tokens: int, output_tokens: int, cost_usd: float, success: bool = True
) -> None:
    """Records one Claude API call for the live activity view."""
    init_db()
    conn = _connect()
    conn.execute(
        """
        INSERT INTO api_calls (created_at, kind, filename, input_tokens, output_tokens, cost_usd, success)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (datetime.now(timezone.utc).isoformat(), kind, filename, input_tokens, output_tokens, cost_usd, int(success)),
    )
    conn.commit()
    conn.close()


def get_recent_api_calls(limit: int = 50) -> list[dict]:
    init_db()
    conn = _connect()
    rows = conn.execute(
        "SELECT created_at, kind, filename, input_tokens, output_tokens, cost_usd, success "
        "FROM api_calls ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_invoice(header: dict, line_items: list[dict], image_filename: str) -> str:
    """Persists a Mick-approved invoice and its line items. Returns the new invoice id."""
    init_db()
    conn = _connect()
    invoice_id = str(uuid.uuid4())

    parsed_date = parse_date(header.get("invoice_date"))
    invoice_date = parsed_date.isoformat() if parsed_date else header.get("invoice_date")

    conn.execute(
        """
        INSERT INTO invoices
            (id, supplier, invoice_number, invoice_date, department, gst, invoice_total, image_filename, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invoice_id,
            header.get("supplier"),
            header.get("invoice_number"),
            invoice_date,
            header.get("department"),
            header.get("gst"),
            header.get("invoice_total"),
            image_filename,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    for item in line_items:
        conn.execute(
            """
            INSERT INTO line_items
                (invoice_id, description, qty, unit, unit_price, line_total, check_flag, check_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice_id,
                item.get("description"),
                item.get("qty"),
                item.get("unit"),
                item.get("unit_price"),
                item.get("line_total"),
                int(bool(item.get("check"))),
                item.get("check_reason"),
            ),
        )
    conn.commit()
    conn.close()
    return invoice_id


def get_last_price(supplier: str, description: str) -> Optional[dict]:
    """Most recent previously-saved unit price for this supplier + item description."""
    if not supplier or not description:
        return None
    init_db()
    conn = _connect()
    row = conn.execute(
        """
        SELECT li.unit_price AS unit_price, inv.invoice_date AS invoice_date
        FROM line_items li
        JOIN invoices inv ON inv.id = li.invoice_id
        WHERE inv.supplier = ? COLLATE NOCASE
          AND li.description = ? COLLATE NOCASE
          AND li.unit_price IS NOT NULL
        ORDER BY inv.invoice_date DESC, inv.created_at DESC
        LIMIT 1
        """,
        (supplier.strip(), description.strip()),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_price_flags(supplier: str, line_items: list[dict], threshold: float = 0.03) -> list[dict]:
    """Flags line items whose unit price moved more than `threshold` since last seen."""
    flags = []
    for item in line_items:
        description = (item.get("description") or "").strip()
        new_price = item.get("unit_price")
        if not description or new_price in (None, ""):
            continue
        try:
            new_price = float(new_price)
        except (TypeError, ValueError):
            continue

        history = get_last_price(supplier, description)
        if history is None or history["unit_price"] in (None,):
            continue

        old_price = float(history["unit_price"])
        if old_price == 0:
            continue

        change = (new_price - old_price) / old_price
        if abs(change) > threshold:
            direction = "up" if change > 0 else "down"
            when = history["invoice_date"] or "a previous invoice"
            flags.append(
                {
                    "level": "warning",
                    "message": (
                        f'"{description}": price {direction} {abs(change) * 100:.1f}% '
                        f"(was ${old_price:.2f} on {when}, now ${new_price:.2f})."
                    ),
                }
            )
    return flags


def get_latest_price_for_description(description: str) -> Optional[dict]:
    """Most recent unit price seen for this item description, from any supplier.

    Used to value stocktake counts, which aren't tied to one supplier.
    """
    if not description:
        return None
    init_db()
    conn = _connect()
    row = conn.execute(
        """
        SELECT li.unit_price AS unit_price, inv.invoice_date AS invoice_date, inv.supplier AS supplier
        FROM line_items li
        JOIN invoices inv ON inv.id = li.invoice_id
        WHERE li.description = ? COLLATE NOCASE
          AND li.unit_price IS NOT NULL
        ORDER BY inv.invoice_date DESC, inv.created_at DESC
        LIMIT 1
        """,
        (description.strip(),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_stocktake(count_date: str, rows: list[dict], source_images: list[str]) -> str:
    """Persists a Mick-approved stocktake count. Returns the new stocktake id."""
    init_db()
    conn = _connect()
    stocktake_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO stocktakes (id, count_date, source_images, created_at) VALUES (?, ?, ?, ?)",
        (stocktake_id, count_date, ",".join(source_images), datetime.now(timezone.utc).isoformat()),
    )
    for row in rows:
        conn.execute(
            """
            INSERT INTO stocktake_items
                (stocktake_id, department, description, counted_qty, unit, unit_price, value, check_flag, check_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stocktake_id,
                row.get("department"),
                row.get("description"),
                row.get("counted_qty"),
                row.get("unit"),
                row.get("unit_price"),
                row.get("value"),
                int(bool(row.get("check"))),
                row.get("check_reason"),
            ),
        )
    conn.commit()
    conn.close()
    return stocktake_id


def get_all_invoices() -> list[dict]:
    """All saved invoices, newest first."""
    init_db()
    conn = _connect()
    rows = conn.execute(
        """
        SELECT id, supplier, invoice_number, invoice_date, department, gst, invoice_total,
               image_filename, created_at
        FROM invoices
        ORDER BY created_at DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_invoice_line_items(invoice_id: str) -> list[dict]:
    init_db()
    conn = _connect()
    rows = conn.execute(
        """
        SELECT description, qty, unit, unit_price, line_total, check_flag, check_reason
        FROM line_items
        WHERE invoice_id = ?
        """,
        (invoice_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_stocktake_value(as_of: Optional[str] = None) -> Optional[dict]:
    """Most recent saved stocktake's total value and by-department split,
    counted on or before `as_of` (an ISO date string) if given."""
    init_db()
    conn = _connect()
    if as_of:
        row = conn.execute(
            "SELECT id, count_date FROM stocktakes WHERE count_date <= ? ORDER BY count_date DESC, created_at DESC LIMIT 1",
            (as_of,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, count_date FROM stocktakes ORDER BY count_date DESC, created_at DESC LIMIT 1"
        ).fetchone()

    if row is None:
        conn.close()
        return None

    items = conn.execute(
        "SELECT department, value FROM stocktake_items WHERE stocktake_id = ?", (row["id"],)
    ).fetchall()
    conn.close()

    total_value = sum(item["value"] or 0 for item in items)
    by_department = {}
    for item in items:
        dept = item["department"] or "unclear"
        by_department[dept] = by_department.get(dept, 0) + (item["value"] or 0)

    return {
        "stocktake_id": row["id"],
        "count_date": row["count_date"],
        "total_value": total_value,
        "by_department": by_department,
    }


def get_all_stocktakes() -> list[dict]:
    """All saved stocktakes, newest first, with their total value."""
    init_db()
    conn = _connect()
    rows = conn.execute("SELECT id, count_date, source_images, created_at FROM stocktakes ORDER BY created_at DESC").fetchall()
    result = []
    for row in rows:
        items = conn.execute(
            "SELECT value FROM stocktake_items WHERE stocktake_id = ?", (row["id"],)
        ).fetchall()
        total_value = sum(item["value"] or 0 for item in items)
        result.append({**dict(row), "total_value": total_value, "item_count": len(items)})
    conn.close()
    return result


def get_stocktake_items(stocktake_id: str) -> list[dict]:
    init_db()
    conn = _connect()
    rows = conn.execute(
        "SELECT department, description, counted_qty, unit, unit_price, value, check_flag, check_reason "
        "FROM stocktake_items WHERE stocktake_id = ?",
        (stocktake_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_invoice(invoice_id: str) -> None:
    """Removes a saved invoice and its line items. The original image file is
    left on disk - only the database record is removed."""
    init_db()
    conn = _connect()
    conn.execute("DELETE FROM line_items WHERE invoice_id = ?", (invoice_id,))
    conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()


def delete_stocktake(stocktake_id: str) -> None:
    """Removes a saved stocktake and its items. Source images are left on disk."""
    init_db()
    conn = _connect()
    conn.execute("DELETE FROM stocktake_items WHERE stocktake_id = ?", (stocktake_id,))
    conn.execute("DELETE FROM stocktakes WHERE id = ?", (stocktake_id,))
    conn.commit()
    conn.close()


def count_unresolved_checks() -> dict:
    """How many saved line items are still marked CHECK - the 'needs attention' badge."""
    init_db()
    conn = _connect()
    invoice_count = conn.execute("SELECT COUNT(*) AS n FROM line_items WHERE check_flag = 1").fetchone()["n"]
    stocktake_count = conn.execute(
        "SELECT COUNT(*) AS n FROM stocktake_items WHERE check_flag = 1"
    ).fetchone()["n"]
    conn.close()
    return {"invoices": invoice_count, "stocktake": stocktake_count, "total": invoice_count + stocktake_count}


def get_unresolved_checks() -> dict:
    """The actual line items still marked CHECK, with enough context to find
    them again - what the 'needs attention' badge count is made of."""
    init_db()
    conn = _connect()
    invoice_rows = conn.execute(
        """
        SELECT inv.id AS invoice_id, inv.supplier AS supplier, inv.invoice_number AS invoice_number,
               inv.invoice_date AS invoice_date, li.description AS description, li.check_reason AS check_reason
        FROM line_items li
        JOIN invoices inv ON inv.id = li.invoice_id
        WHERE li.check_flag = 1
        ORDER BY inv.invoice_date DESC
        """
    ).fetchall()
    stocktake_rows = conn.execute(
        """
        SELECT st.id AS stocktake_id, st.count_date AS count_date, si.department AS department,
               si.description AS description, si.check_reason AS check_reason
        FROM stocktake_items si
        JOIN stocktakes st ON st.id = si.stocktake_id
        WHERE si.check_flag = 1
        ORDER BY st.count_date DESC
        """
    ).fetchall()
    conn.close()
    return {
        "invoices": [dict(r) for r in invoice_rows],
        "stocktake": [dict(r) for r in stocktake_rows],
    }


def get_line_item_totals_for_period(start: str, end: str) -> list[dict]:
    """Total spend per item description for invoices dated between start and
    end (inclusive, ISO YYYY-MM-DD) - the 'food item breakdown' report."""
    init_db()
    conn = _connect()
    rows = conn.execute(
        """
        SELECT li.description AS description, SUM(li.line_total) AS total
        FROM line_items li
        JOIN invoices inv ON inv.id = li.invoice_id
        WHERE inv.invoice_date BETWEEN ? AND ? AND li.line_total IS NOT NULL
        GROUP BY li.description
        ORDER BY total DESC
        """,
        (start, end),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def backup_if_needed(keep_days: int = 30) -> None:
    """Copies the database to data/backups/ once per day, pruning backups
    older than `keep_days`. Only runs while the app is actually opened -
    there's no background scheduler, so a day with no usage has no backup."""
    if not DB_PATH.exists():
        return

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    target = BACKUPS_DIR / f"invoices_{today}.db"
    if target.exists():
        return

    shutil.copy2(DB_PATH, target)

    cutoff = date.today() - timedelta(days=keep_days)
    for f in BACKUPS_DIR.glob("invoices_*.db"):
        try:
            backup_date = datetime.strptime(f.stem.replace("invoices_", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if backup_date < cutoff:
            f.unlink()
