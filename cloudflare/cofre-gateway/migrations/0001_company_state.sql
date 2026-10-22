CREATE TABLE cofre_company_state (
    company TEXT PRIMARY KEY NOT NULL CHECK(length(company) BETWEEN 1 AND 64),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    state TEXT NOT NULL CHECK(json_valid(state) AND length(CAST(state AS BLOB)) <= 1048576),
    updated_at INTEGER NOT NULL
) WITHOUT ROWID;
