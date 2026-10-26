"""
movies_ingestion.py
-------------------
Data ingestion pipeline for imdb_top_1000.csv -> PostgreSQL (titles_top_100).

Sub-Task 2: CSV parsing and data cleaning.
Sub-Task 3: Database connection and DDL execution.
Sub-Task 4: Batch ingestion and error handling.
Sub-Task 5: Pipeline verification.
"""

import csv
import os
import re
import logging
from pathlib import Path
from uuid import uuid4
import psycopg
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_SQL_PATH = "db/movies.sql"
CONNECT_TIMEOUT_SECONDS = 10

# ---------------------------------------------------------------------------
# Target columns (matches db/movies.sql, Poster_Link and Star1-Star4 excluded)
# ---------------------------------------------------------------------------
TARGET_COLUMNS = (
    "MovieId",
    "Series_Title",
    "Released_Year",
    "Certificate",
    "Runtime",
    "Genre",
    "IMDB_Rating",
    "Overview",
    "Meta_score",
    "Director",
    "votes_count",
    "Gross",
)


# ---------------------------------------------------------------------------
# Field-level transformation helpers
# ---------------------------------------------------------------------------

def parse_runtime(value: str) -> int | None:
    """Extract leading integer from a string like '142 min'.

    Returns None if no digits are found.
    """
    value = value.strip()
    match = re.match(r"(\d+)", value)
    return int(match.group(1)) if match else None


def parse_gross(value: str) -> int | None:
    """Strip commas and convert to int.

    Returns None for empty / non-numeric strings.
    """
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def parse_meta_score(value: str) -> int | None:
    """Convert to int; return None when the field is empty or non-numeric."""
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(float(stripped))
    except ValueError:
        return None


def parse_released_year(value: str) -> int | None:
    """Safely convert Released_Year to int.

    Handles:
    - Normal year strings like '1994' -> 1994
    - Strings wrapped in extra quotes like "'PG'" -> None (no digit sequence of 4)
    - Any other non-numeric string -> attempt to extract a 4-digit year; else None
    """
    stripped = value.strip().strip("'\"")
    if not stripped:
        return None
    # Prefer a 4-digit year found anywhere in the value
    match = re.search(r"\b(\d{4})\b", stripped)
    if match:
        return int(match.group(1))
    # Fall back to any leading digits
    match = re.match(r"(\d+)", stripped)
    if match:
        return int(match.group(1))
    return None


def parse_imdb_rating(value: str) -> float | None:
    """Convert to float; return None for empty / non-numeric."""
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def parse_no_of_votes(value: str) -> int | None:
    """Convert to int; return None for empty / non-numeric."""
    stripped = value.strip().replace(",", "")
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        return None


def parse_string(value: str) -> str | None:
    """Trim whitespace; return None for blank strings."""
    stripped = value.strip()
    return stripped if stripped else None


# ---------------------------------------------------------------------------
# Row-level transformation
# ---------------------------------------------------------------------------

def transform_row(row: dict) -> dict:
    """Map a raw CSV row dict to the target schema."""
    return {
        "MovieId": parse_string(row.get("MovieId", "")) if "MovieId" in row else None,
        "Series_Title": parse_string(row.get("Series_Title", "")),
        "Released_Year": parse_released_year(row.get("Released_Year", "")),
        "Certificate": parse_string(row.get("Certificate", "")),
        "Runtime": parse_runtime(row.get("Runtime", "")),
        "Genre": parse_string(row.get("Genre", "")),
        "IMDB_Rating": parse_imdb_rating(row.get("IMDB_Rating", "")),
        "Overview": parse_string(row.get("Overview", "")),
        "Meta_score": parse_meta_score(row.get("Meta_score", "")),
        "Director": parse_string(row.get("Director", "")),
        # The source CSV header is still "No_of_Votes"; the target column is votes_count.
        "votes_count": parse_no_of_votes(row.get("No_of_Votes", "")),
        "Gross": parse_gross(row.get("Gross", "")),
    }


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

def load_and_clean_csv(filepath: str) -> list[dict]:
    """Read *filepath*, clean every row, and return a list of cleaned dicts.

    Only the 12 TARGET_COLUMNS are retained; Poster_Link and Star1-Star4 are dropped.
    """
    cleaned_rows: list[dict] = []
    with open(filepath, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw_row in reader:
            cleaned_rows.append(transform_row(raw_row))
    logger.info("Loaded and cleaned %d rows from %s", len(cleaned_rows), filepath)
    return cleaned_rows


# ---------------------------------------------------------------------------
# Database connection and schema creation
# ---------------------------------------------------------------------------

def get_connection_params() -> dict:
    """Build psycopg connection kwargs from PG* environment variables.

    Non-secret values fall back to local defaults. The password is never
    hardcoded; it is only passed when PGPASSWORD is set in the environment.
    """
    params = {
        "host": os.environ.get("PGHOST", "127.0.0.1"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "user": os.environ.get("PGUSER", "postgres"),
        "dbname": os.environ.get("PGDATABASE", "postgres"),
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
    }
    password = os.environ.get("PGPASSWORD")
    if password:
        params["password"] = password
    return params


def get_connection() -> psycopg.Connection:
    """Open a PostgreSQL connection using environment-based configuration.

    Raises psycopg.OperationalError if the database cannot be reached. The
    error is logged without credentials.
    """
    params = get_connection_params()
    try:
        conn = psycopg.connect(**params)
    except psycopg.OperationalError:
        logger.error(
            "Could not connect to PostgreSQL at %s:%s (db=%s, user=%s)",
            params["host"], params["port"], params["dbname"], params["user"],
        )
        raise
    logger.info(
        "Connected to PostgreSQL at %s:%s (db=%s)",
        params["host"], params["port"], params["dbname"],
    )
    return conn


def create_schema(conn: psycopg.Connection, sql_path: str = DEFAULT_SQL_PATH) -> None:
    """Execute the DDL in *sql_path* (idempotent: CREATE TABLE IF NOT EXISTS)."""
    ddl = Path(sql_path).read_text(encoding="utf-8")
    with conn.transaction():
        conn.execute(ddl)
    logger.info("Schema from %s applied", sql_path)


# ---------------------------------------------------------------------------
# Batch insertion and pipeline orchestration
# ---------------------------------------------------------------------------

# Column names come from the fixed TARGET_COLUMNS constant (never user input);
# all values are bound via %s placeholders.
INSERT_SQL = (
    f"INSERT INTO titles_top_100 ({', '.join(TARGET_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(TARGET_COLUMNS))})"
)


def insert_movies_batch(
    conn: psycopg.Connection, records: list[dict], batch_size: int = 100
) -> int:
    """Insert *records* into titles_top_100 in batches using parameterized SQL.

    Does not commit; the caller owns the transaction. Returns rows inserted.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be a positive integer")

    total = len(records)
    inserted = 0
    with conn.cursor() as cur:
        for start in range(0, total, batch_size):
            batch = records[start:start + batch_size]
            params = [tuple(rec[col] for col in TARGET_COLUMNS) for rec in batch]
            cur.executemany(INSERT_SQL, params)
            inserted += len(batch)
            logger.info("Inserted batch: %d/%d rows", inserted, total)
    return inserted

def create_movie_id(records: list[dict])-> list[dict]:
    tmp_records: list[dict] = []
    for record in records:
        record["MovieId"] = str(uuid4())
        tmp_records.append(record)
    return tmp_records



def run_ingestion_pipeline(
    csv_path: str = "data/imdb_top_1000.csv",
    sql_path: str = DEFAULT_SQL_PATH,
    batch_size: int = 100,
) -> int:
    """Run the full CSV -> PostgreSQL ingestion and return the rows inserted.

    All inserts happen in a single transaction: on any error everything is
    rolled back, the error is logged, and the exception is re-raised.
    """
    conn = None
    try:
        conn = get_connection()
        create_schema(conn, sql_path)
        records = load_and_clean_csv(csv_path)
        # Append MovieId
        records = create_movie_id(records)
        with conn.transaction():
            inserted = insert_movies_batch(conn, records, batch_size)
        logger.info("Ingestion complete: %d rows inserted into titles_top_100", inserted)
        return inserted
    except Exception:
        logger.error("Ingestion failed; transaction rolled back", exc_info=True)
        raise
    finally:
        if conn is not None:
            conn.close()
            logger.info("Database connection closed")


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_parse_helpers() -> None:
    """Unit-check every transformation helper against known values."""
    # parse_runtime
    _assert(parse_runtime("142 min") == 142, "parse_runtime: '142 min'")
    _assert(parse_runtime("  90 min  ") == 90, "parse_runtime: leading spaces")
    _assert(parse_runtime("") is None, "parse_runtime: empty string")

    # parse_gross
    _assert(parse_gross("28,341,469") == 28341469, "parse_gross: comma-separated")
    _assert(parse_gross("") is None, "parse_gross: empty string")
    _assert(parse_gross("0") == 0, "parse_gross: zero")

    # parse_meta_score
    _assert(parse_meta_score("80") == 80, "parse_meta_score: numeric string")
    _assert(parse_meta_score("") is None, "parse_meta_score: empty string")
    _assert(parse_meta_score("80.0") == 80, "parse_meta_score: float string")

    # parse_released_year
    _assert(parse_released_year("1994") == 1994, "parse_released_year: normal")
    _assert(parse_released_year("'PG'") is None, "parse_released_year: 'PG'")
    _assert(parse_released_year("") is None, "parse_released_year: empty")
    _assert(parse_released_year("  2001  ") == 2001, "parse_released_year: padded")

    # parse_imdb_rating
    _assert(parse_imdb_rating("9.3") == 9.3, "parse_imdb_rating: float")
    _assert(parse_imdb_rating("") is None, "parse_imdb_rating: empty")

    # parse_no_of_votes
    _assert(parse_no_of_votes("2343110") == 2343110, "parse_no_of_votes: numeric")
    _assert(parse_no_of_votes("") is None, "parse_no_of_votes: empty")

    # parse_string
    _assert(parse_string("  Drama  ") == "Drama", "parse_string: trim")
    _assert(parse_string("") is None, "parse_string: empty -> None")
    _assert(parse_string("   ") is None, "parse_string: whitespace -> None")

    print("  All helper assertions passed.")


def verify_csv(filepath: str = "data/imdb_top_1000.csv") -> None:
    """Load, clean, and verify the CSV produces exactly 1000 rows with correct types."""
    rows = load_and_clean_csv(filepath)

    # Row count
    _assert(len(rows) == 1000, f"Expected 1000 rows, got {len(rows)}")

    # Every row must have exactly the TARGET_COLUMNS keys
    for i, row in enumerate(rows):
        _assert(
            set(row.keys()) == set(TARGET_COLUMNS),
            f"Row {i} has unexpected keys: {set(row.keys()) - set(TARGET_COLUMNS)}",
        )

    # Type spot-checks on first row (The Shawshank Redemption)
    first = rows[0]
    _assert(first["Runtime"] == 142, f"Row 0 Runtime: {first['Runtime']}")
    _assert(first["Gross"] == 28341469, f"Row 0 Gross: {first['Gross']}")
    _assert(first["Released_Year"] == 1994, f"Row 0 Released_Year: {first['Released_Year']}")
    _assert(isinstance(first["IMDB_Rating"], float), "Row 0 IMDB_Rating should be float")
    _assert(isinstance(first["votes_count"], int), "Row 0 votes_count should be int")

    # Nullable fields must be None (not empty string) when absent
    none_meta = [r for r in rows if r["Meta_score"] is None]
    none_gross = [r for r in rows if r["Gross"] is None]
    _assert(len(none_meta) > 0, "Expected some rows with Meta_score=None")
    _assert(len(none_gross) > 0, "Expected some rows with Gross=None")

    # No Star columns leaked through
    for i, row in enumerate(rows):
        for star in ("Star1", "Star2", "Star3", "Star4"):
            _assert(star not in row, f"Row {i} contains unexpected column {star}")

    print(
        f"  CSV verification passed: {len(rows)} rows, "
        f"{len(none_meta)} Meta_score=None, {len(none_gross)} Gross=None."
    )


def verify_database() -> None:
    """Connect, apply the DDL twice (idempotency), and confirm the table exists.

    If the database is offline, report it and skip instead of failing.
    """
    try:
        conn = get_connection()
    except psycopg.OperationalError:
        print("  Database unreachable; skipping DDL verification.")
        return
    with conn:
        create_schema(conn)
        create_schema(conn)  # must be safe to run repeatedly
        row = conn.execute("SELECT to_regclass('public.titles_top_100')").fetchone()
        _assert(row is not None and row[0] == "titles_top_100", "titles_top_100 missing")
        cols = conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'titles_top_100'"
        ).fetchone()
        _assert(cols is not None and cols[0] == len(TARGET_COLUMNS), f"Unexpected column count: {cols}")
    print("  Database verification passed: titles_top_100 exists with 12 columns.")


def verify_pipeline() -> None:
    """Perform end-to-end ingestion pipeline verification against PostgreSQL.

    1. Connects to PostgreSQL and truncates titles_top_100 for a clean test.
    2. Runs run_ingestion_pipeline() to ingest data from CSV.
    3. Verifies total inserted row count is exactly 1,000.
    4. Verifies column data types in the information schema.
    5. Verifies sample record data integrity and types.
    6. Verifies NULL handling for Meta_score, Gross, and Certificate.
    """
    try:
        conn = get_connection()
    except psycopg.OperationalError:
        print("  Database unreachable; skipping pipeline verification.")
        return

    with conn:
        create_schema(conn)
        with conn.transaction():
            conn.execute("TRUNCATE TABLE titles_top_100")
        logger.info("Cleaned titles_top_100 table for fresh verification")

    # Execute full pipeline
    inserted = run_ingestion_pipeline()
    _assert(inserted == 1000, f"Expected 1000 inserted rows, got {inserted}")

    # Re-open connection to verify DB state
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Total row count check
            cur.execute("SELECT count(*) FROM titles_top_100")
            count_res = cur.fetchone()
            _assert(count_res is not None and count_res[0] == 1000, f"Expected 1000 rows in DB, got {count_res}")

            # 2. Schema / data type validation
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'titles_top_100'"
            )
            type_map = {row[0].lower(): row[1].lower() for row in cur.fetchall()}
            _assert(type_map.get("runtime") == "bigint", f"Runtime type: {type_map.get('runtime')}")
            _assert(type_map.get("gross") == "bigint", f"Gross type: {type_map.get('gross')}")
            _assert(type_map.get("meta_score") == "bigint", f"Meta_score type: {type_map.get('meta_score')}")
            _assert(type_map.get("released_year") == "bigint", f"Released_Year type: {type_map.get('released_year')}")
            _assert(type_map.get("imdb_rating") == "double precision", f"IMDB_Rating type: {type_map.get('imdb_rating')}")
            _assert(type_map.get("votes_count") == "bigint", f"votes_count type: {type_map.get('votes_count')}")

            # 3. Sample record verification (The Shawshank Redemption)
            cur.execute(
                "SELECT Series_Title, Released_Year, Certificate, Runtime, "
                "Genre, IMDB_Rating, Overview, Meta_score, Director, votes_count, Gross "
                "FROM titles_top_100 WHERE Series_Title = 'The Shawshank Redemption'"
            )
            sample = cur.fetchone()
            _assert(sample is not None, "Sample record 'The Shawshank Redemption' not found")
            _assert(sample[0] == "The Shawshank Redemption", "Title mismatch")
            _assert(sample[1] == 1994 and isinstance(sample[1], int), "Released_Year should be int 1994")
            _assert(sample[2] == "A", "Certificate mismatch")
            _assert(sample[3] == 142 and isinstance(sample[3], int), "Runtime should be int 142")
            _assert(sample[4] == "Drama", "Genre mismatch")
            _assert(sample[5] == 9.3 and isinstance(sample[5], float), "IMDB_Rating should be float 9.3")
            _assert(sample[7] == 80 and isinstance(sample[7], int), "Meta_score should be int 80")
            _assert(sample[8] == "Frank Darabont", "Director mismatch")
            _assert(sample[9] == 2343110 and isinstance(sample[9], int), "votes_count should be int 2343110")
            _assert(sample[10] == 28341469 and isinstance(sample[10], int), "Gross should be int 28341469")

            # 4. Nullable fields verification
            cur.execute("SELECT count(*) FROM titles_top_100 WHERE Meta_score IS NULL")
            null_meta = cur.fetchone()
            _assert(null_meta is not None and null_meta[0] == 157, f"Expected 157 NULL Meta_scores, got {null_meta}")

            cur.execute("SELECT count(*) FROM titles_top_100 WHERE Gross IS NULL")
            null_gross = cur.fetchone()
            _assert(null_gross is not None and null_gross[0] == 169, f"Expected 169 NULL Gross, got {null_gross}")

            cur.execute("SELECT count(*) FROM titles_top_100 WHERE Certificate IS NULL")
            null_cert = cur.fetchone()
            _assert(null_cert is not None and null_cert[0] == 101, f"Expected 101 NULL Certificate, got {null_cert}")

            # 5. Verify sample record containing NULL values
            cur.execute(
                "SELECT Series_Title, Meta_score, Gross FROM titles_top_100 WHERE Meta_score IS NULL LIMIT 1"
            )
            null_sample = cur.fetchone()
            _assert(null_sample is not None and null_sample[1] is None, "Expected None for NULL Meta_score")

    print("  Pipeline verification passed: 1000 rows loaded, types & nulls validated.")


def verify_all() -> None:
    """Run all verification suites."""
    print("Running parse helpers verification...")
    verify_parse_helpers()
    print("Running CSV verification...")
    verify_csv()
    print("Running database DDL verification...")
    verify_database()
    print("Running end-to-end pipeline verification...")
    verify_pipeline()
    print("All verifications successfully passed!")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if "--verify" in sys.argv:
        verify_all()
    else:
        run_ingestion_pipeline()
