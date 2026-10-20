import pytest
from movies_ingestion import TARGET_COLUMNS, load_and_clean_csv


class TestCSVLoader:
    """Tests for dataset loading, sanitization, and structure validation."""

    CSV_PATH = "data/imdb_top_1000.csv"

    def test_load_and_clean_csv_row_count(self):
        rows = load_and_clean_csv(self.CSV_PATH)
        assert len(rows) == 1000

    def test_load_and_clean_csv_schema_projection(self):
        rows = load_and_clean_csv(self.CSV_PATH)
        for i, row in enumerate(rows):
            assert tuple(row.keys()) == TARGET_COLUMNS, f"Row {i} keys do not match TARGET_COLUMNS"
            for star_col in ("Star1", "Star2", "Star3", "Star4"):
                assert star_col not in row

    def test_load_and_clean_csv_sample_fidelity(self):
        rows = load_and_clean_csv(self.CSV_PATH)
        first_row = rows[0]

        assert first_row["Series_Title"] == "The Shawshank Redemption"
        assert first_row["Released_Year"] == 1994
        assert first_row["Certificate"] == "A"
        assert first_row["Runtime"] == 142
        assert first_row["Genre"] == "Drama"
        assert first_row["IMDB_Rating"] == pytest.approx(9.3)
        assert first_row["Meta_score"] == 80
        assert first_row["Director"] == "Frank Darabont"
        assert first_row["No_of_Votes"] == 2343110
        assert first_row["Gross"] == 28341469

    def test_load_and_clean_csv_nullable_fields(self):
        rows = load_and_clean_csv(self.CSV_PATH)
        none_meta = [r for r in rows if r["Meta_score"] is None]
        none_gross = [r for r in rows if r["Gross"] is None]
        none_cert = [r for r in rows if r["Certificate"] is None]

        assert len(none_meta) == 157
        assert len(none_gross) == 169
        assert len(none_cert) == 101
