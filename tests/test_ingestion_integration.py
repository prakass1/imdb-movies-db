import psycopg
import pytest
from movies_ingestion import (
    TARGET_COLUMNS,
    create_schema,
    get_connection,
    get_connection_params,
    insert_movies_batch,
    load_and_clean_csv,
    run_ingestion_pipeline,
)


def is_postgres_available() -> bool:
    try:
        conn = get_connection()
        conn.close()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not is_postgres_available(),
    reason="PostgreSQL database service is not accessible",
)


@requires_postgres
class TestIngestionIntegration:
    """Integration tests executing against a live PostgreSQL database."""

    @pytest.fixture(autouse=True)
    def setup_db(self):
        with get_connection() as conn:
            create_schema(conn)
            with conn.transaction():
                conn.execute("TRUNCATE TABLE titles_top_100")
        yield

    def test_create_schema_idempotence(self):
        with get_connection() as conn:
            create_schema(conn)
            create_schema(conn)

            row = conn.execute("SELECT to_regclass('public.titles_top_100')").fetchone()
            assert row is not None and row[0] == "titles_top_100"

            cols = conn.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'titles_top_100'"
            ).fetchone()
            assert cols is not None and cols[0] == len(TARGET_COLUMNS)

    def test_insert_movies_batch_transaction_rollback(self):
        invalid_records = [
            {col: None for col in TARGET_COLUMNS},
        ]
        # Certificate exceeds VARCHAR(10)
        invalid_records[0]["Certificate"] = "A" * 50

        with get_connection() as conn:
            with pytest.raises(Exception):
                with conn.transaction():
                    insert_movies_batch(conn, invalid_records)

            count = conn.execute("SELECT count(*) FROM titles_top_100").fetchone()
            assert count is not None and count[0] == 0

    def test_run_ingestion_pipeline_end_to_end(self):
        inserted = run_ingestion_pipeline()
        assert inserted == 1000

        with get_connection() as conn:
            count = conn.execute("SELECT count(*) FROM titles_top_100").fetchone()
            assert count is not None and count[0] == 1000

            # Verify sample record
            sample = conn.execute(
                "SELECT Poster_Link, Series_Title, Released_Year, Certificate, Runtime, "
                "Genre, IMDB_Rating, Overview, Meta_score, Director, No_of_Votes, Gross "
                "FROM titles_top_100 WHERE Series_Title = 'The Shawshank Redemption'"
            ).fetchone()
            assert sample is not None
            assert sample[1] == "The Shawshank Redemption"
            assert sample[2] == 1994
            assert sample[3] == "A"
            assert sample[4] == 142
            assert sample[5] == "Drama"
            assert sample[6] == pytest.approx(9.3)
            assert sample[8] == 80
            assert sample[9] == "Frank Darabont"
            assert sample[10] == 2343110
            assert sample[11] == 28341469
