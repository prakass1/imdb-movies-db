CREATE TABLE IF NOT EXISTS titles_top_100 (
    MovieId VARCHAR(255),
    Poster_Link TEXT,
    Series_Title VARCHAR(255),
    Released_Year BIGINT,
    Certificate VARCHAR(10),
    Runtime BIGINT,
    Genre VARCHAR(255),
    IMDB_Rating DOUBLE PRECISION,
    Overview TEXT,
    Meta_score BIGINT,
    Director VARCHAR(255),
    No_of_Votes BIGINT,
    Gross BIGINT
);
