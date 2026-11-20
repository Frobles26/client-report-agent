"""Create the database tables without needing psql.

Run `python -m report_agent.setup_db` to run every file in sql/ in order.
Safe to run more than once: the SQL only creates what is missing.
"""

from pathlib import Path

from sqlalchemy import text

from report_agent.db import get_engine

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def main() -> None:
    files = sorted(SQL_DIR.glob("*.sql"))
    if not files:
        raise SystemExit(f"No .sql files found in {SQL_DIR}")
    engine = get_engine()
    try:
        with engine.begin() as conn:
            for path in files:
                conn.execute(text(path.read_text(encoding="utf-8")))
                print(f"Ran {path.name}")
    except Exception as exc:  # connection refused, wrong password, SQL error
        raise SystemExit(f"Schema setup failed: {exc}") from exc
    print("Schema ready.")


if __name__ == "__main__":
    main()
