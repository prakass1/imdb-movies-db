# Plan: Movies Database Ingestion Pipeline

## Top-Level Overview
Build an end-to-end data ingestion pipeline in `movies_ingestion.py` that reads movie dataset records from `data/imdb_top_1000.csv`, performs necessary data sanitization and transformations, initializes the PostgreSQL database and table based on `db/movies.sql`, and inserts the structured records into PostgreSQL.

---

## Sub-Tasks

### Sub-Task 1: PostgreSQL DDL Alignment
- **Intent**: Update `db/movies.sql` with valid PostgreSQL DDL syntax and appropriate column length constraints.
- **Expected Outcomes**:
  - `db/movies.sql` contains clean, valid PostgreSQL DDL.
  - Column length definitions (such as `Certificate VARCHAR(10)`) prevent truncation errors on data insertion.
- **Todo List**:
  1. Fix PostgreSQL database and table creation syntax in `db/movies.sql`.
  2. Adjust data types and field lengths for the 12 defined columns to match the dataset format.
- **Relevant Context**:
  - `db/movies.sql`
- **Status**: [x] done

---

### Sub-Task 2: CSV Parsing and Data Cleaning
- **Intent**: Extract and sanitize data from `data/imdb_top_1000.csv` to match the 12-column schema.
- **Expected Outcomes**:
  - `Runtime` numeric extraction (e.g., `"142 min"` -> `142`).
  - `Gross` currency string formatting removal (e.g., `"28,341,469"` -> `28341469`).
  - Missing/NaN values in nullable fields (such as `Meta_score` and `Gross`) handled safely.
  - Non-schema columns (`Star1`, `Star2`, `Star3`, `Star4`) filtered out.
- **Todo List**:
  1. Implement CSV loading logic in `movies_ingestion.py`.
  2. Implement data transformation and cleansing functions for `Runtime`, `Gross`, `Meta_score`, and string fields.
  3. Filter records to match the 12 target columns.
- **Relevant Context**:
  - `data/imdb_top_1000.csv`
  - `movies_ingestion.py`
  - Note: Ensure `Released_Year` handles non-numeric entries (like `"PG"`) safely by coercing to `None`/`NULL` or parsing digits.
- **Status**: [x] done

---

### Sub-Task 3: Database Connection and DDL Execution
- **Intent**: Establish secure connection to PostgreSQL service using environment variables and execute table creation DDL.
- **Expected Outcomes**:
  - Secure credential retrieval from environment variables (`PGUSER`, `PGPASSWORD`, `PGHOST`, `PGPORT`, `PGDATABASE`).
  - Target database connection verified.
  - DDL from `db/movies.sql` executed to ensure `titles_top_100` table exists.
- **Todo List**:
  1. Add environment variable loading and database connection initialization in `movies_ingestion.py`.
  2. Implement schema execution logic to create the table if it does not already exist.
- **Relevant Context**:
  - `docker-compose.yaml`
  - `db/movies.sql`
  - `movies_ingestion.py`
- **Status**: [x] done

---

### Sub-Task 4: Batch Ingestion and Error Handling
- **Intent**: Insert sanitized movie records into `titles_top_100` using parameterized queries.
- **Expected Outcomes**:
  - All cleaned records inserted into `titles_top_100` efficiently.
  - Parameterized queries used to prevent injection.
  - Structured logging for ingestion progress and status.
- **Todo List**:
  1. Implement parameterized batch insertion logic in `movies_ingestion.py`.
  2. Implement structured logging and exception handling.
  3. Close database connections and clean up resources safely.
- **Relevant Context**:
  - `movies_ingestion.py`
- **Status**: [x] done

---

### Sub-Task 5: Pipeline Verification
- **Intent**: Validate that the ingestion pipeline executes successfully and loads accurate data into PostgreSQL.
- **Expected Outcomes**:
  - Ingestion script completes without errors.
  - Database contains expected row counts and correct column data.
- **Todo List**:
  1. Run the ingestion pipeline.
  2. Query row count and sample rows to verify data fidelity and integrity.
- **Relevant Context**:
  - `movies_ingestion.py`
  - `db/movies.sql`
- **Status**: [x] done
