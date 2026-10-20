import pytest
from movies_ingestion import (
    TARGET_COLUMNS,
    get_connection_params,
    parse_gross,
    parse_imdb_rating,
    parse_meta_score,
    parse_no_of_votes,
    parse_released_year,
    parse_runtime,
    parse_string,
    transform_row,
)


class TestParseHelpers:
    """Unit tests for individual field parsing functions."""

    def test_parse_runtime(self):
        assert parse_runtime("142 min") == 142
        assert parse_runtime("  90 min  ") == 90
        assert parse_runtime("") is None
        assert parse_runtime("invalid") is None

    def test_parse_gross(self):
        assert parse_gross("28,341,469") == 28341469
        assert parse_gross("0") == 0
        assert parse_gross("") is None
        assert parse_gross("   ") is None
        assert parse_gross("N/A") is None

    def test_parse_meta_score(self):
        assert parse_meta_score("80") == 80
        assert parse_meta_score("80.0") == 80
        assert parse_meta_score("") is None
        assert parse_meta_score("   ") is None
        assert parse_meta_score("abc") is None

    def test_parse_released_year(self):
        assert parse_released_year("1994") == 1994
        assert parse_released_year("  2001  ") == 2001
        assert parse_released_year("'PG'") is None
        assert parse_released_year("") is None
        assert parse_released_year("None") is None

    def test_parse_imdb_rating(self):
        assert parse_imdb_rating("9.3") == pytest.approx(9.3)
        assert parse_imdb_rating("") is None
        assert parse_imdb_rating("   ") is None
        assert parse_imdb_rating("invalid") is None

    def test_parse_no_of_votes(self):
        assert parse_no_of_votes("2,343,110") == 2343110
        assert parse_no_of_votes("2343110") == 2343110
        assert parse_no_of_votes("") is None
        assert parse_no_of_votes("   ") is None

    def test_parse_string(self):
        assert parse_string("  Drama  ") == "Drama"
        assert parse_string("") is None
        assert parse_string("   ") is None
        assert parse_string("The Dark Knight") == "The Dark Knight"


class TestRowTransformation:
    """Unit tests for row-level transformation and column projection."""

    def test_transform_row_complete(self):
        raw_row = {
            "Poster_Link": "https://example.com/poster.jpg",
            "Series_Title": "The Shawshank Redemption",
            "Released_Year": "1994",
            "Certificate": "A",
            "Runtime": "142 min",
            "Genre": "Drama",
            "IMDB_Rating": "9.3",
            "Overview": "Two imprisoned men bond over a number of years...",
            "Meta_score": "80",
            "Director": "Frank Darabont",
            "Star1": "Tim Robbins",
            "Star2": "Morgan Freeman",
            "Star3": "Bob Gunton",
            "Star4": "William Sadler",
            "No_of_Votes": "2343110",
            "Gross": "28,341,469",
        }
        transformed = transform_row(raw_row)

        assert tuple(transformed.keys()) == TARGET_COLUMNS
        assert transformed["Series_Title"] == "The Shawshank Redemption"
        assert transformed["Released_Year"] == 1994
        assert transformed["Runtime"] == 142
        assert transformed["IMDB_Rating"] == pytest.approx(9.3)
        assert transformed["Meta_score"] == 80
        assert transformed["Gross"] == 28341469
        assert transformed["No_of_Votes"] == 2343110
        assert "Star1" not in transformed
        assert "Star2" not in transformed

    def test_transform_row_empty_values(self):
        raw_row = {}
        transformed = transform_row(raw_row)

        assert tuple(transformed.keys()) == TARGET_COLUMNS
        assert all(val is None for val in transformed.values())


class TestConnectionParams:
    """Unit tests for connection parameters resolution."""

    def test_get_connection_params_defaults(self, monkeypatch):
        monkeypatch.delenv("PGHOST", raising=False)
        monkeypatch.delenv("PGPORT", raising=False)
        monkeypatch.delenv("PGUSER", raising=False)
        monkeypatch.delenv("PGDATABASE", raising=False)
        monkeypatch.delenv("PGPASSWORD", raising=False)

        params = get_connection_params()
        assert params["host"] == "127.0.0.1"
        assert params["port"] == 5432
        assert params["user"] == "postgres"
        assert params["dbname"] == "postgres"
        assert "password" not in params

    def test_get_connection_params_custom_env(self, monkeypatch):
        monkeypatch.setenv("PGHOST", "db.internal")
        monkeypatch.setenv("PGPORT", "5433")
        monkeypatch.setenv("PGUSER", "app_user")
        monkeypatch.setenv("PGDATABASE", "movies")
        monkeypatch.setenv("PGPASSWORD", "secret123")

        params = get_connection_params()
        assert params["host"] == "db.internal"
        assert params["port"] == 5433
        assert params["user"] == "app_user"
        assert params["dbname"] == "movies"
        assert params["password"] == "secret123"
