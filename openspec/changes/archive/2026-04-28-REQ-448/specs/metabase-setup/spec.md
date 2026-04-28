## ADDED Requirements

### Requirement: setup_metabase.py provisions Metabase from SQL files

The system SHALL provide `observability/setup_metabase.py`, a standalone Python
CLI that provisions Metabase (v0.50+) by reading SQL from
`observability/queries/sisyphus/` and calling the Metabase REST API. The script
MUST be idempotent: re-running it against an already-provisioned Metabase SHALL
produce no duplicate questions or dashboards.

#### Scenario: MBS-S1 load_sql returns content for every Q1-Q18 SQL file

- **GIVEN** the 18 SQL files exist under `observability/queries/sisyphus/`
- **WHEN** `load_sql(filename)` is called for each `QuestionSpec.filename`
- **THEN** the function returns a non-empty string for each file

#### Scenario: MBS-S2 login stores session token

- **GIVEN** a MetabaseClient pointed at a Metabase instance
- **WHEN** `login(user, password)` is called and Metabase returns `{"id": "<token>"}`
- **THEN** `client._token` is set to `"<token>"`
- **AND** subsequent API calls include `X-Metabase-Session: <token>` header

#### Scenario: MBS-S3 get_or_create_card creates when not found, skips when found

- **GIVEN** a MetabaseClient where `GET /api/card` returns an existing list
- **WHEN** `get_or_create_card(name, ...)` is called for a name NOT in the list
- **THEN** `POST /api/card` is issued and `(new_id, True)` is returned
- **WHEN** `get_or_create_card(name, ...)` is called for a name that IS in the list
- **THEN** no `POST /api/card` is issued and `(existing_id, False)` is returned

#### Scenario: MBS-S4 force=True updates existing card

- **GIVEN** a card with the target name already exists
- **WHEN** `get_or_create_card(name, ..., force=True)` is called
- **THEN** `PUT /api/card/<id>` is issued with the updated SQL and display
- **AND** `(existing_id, False)` is returned (not created, but updated)

#### Scenario: MBS-S5 get_or_create_dashboard creates with layout cards

- **GIVEN** `GET /api/dashboard` returns an empty list
- **WHEN** `get_or_create_dashboard(name, card_id_map, layout)` is called
- **THEN** `POST /api/dashboard` creates the dashboard
- **AND** `PUT /api/dashboard/<id>/cards` is called with cards matching the layout

#### Scenario: MBS-S6 get_or_create_dashboard skips existing unless force

- **GIVEN** `GET /api/dashboard` returns a dashboard with the target name
- **WHEN** `get_or_create_dashboard(name, ..., force=False)` is called
- **THEN** no `POST /api/dashboard` is issued
- **AND** `(existing_id, False)` is returned

#### Scenario: MBS-S7 provision returns correct created/skipped counts

- **GIVEN** one question already exists and 17 do not; no dashboards exist
- **WHEN** `provision(client, ...)` is called
- **THEN** `result.questions_created == 17` and `result.questions_skipped == 1`
- **AND** `result.dashboards_created == 3` and `result.dashboards_skipped == 0`

#### Scenario: MBS-S8 dry_run makes no HTTP calls

- **GIVEN** a MetabaseClient with a recording HTTP stub
- **WHEN** `provision(client, ..., dry_run=True)` is called
- **THEN** zero HTTP calls are recorded

#### Scenario: MBS-S9 QUESTIONS has exactly 18 entries numbered 1 to 18

- **GIVEN** the `QUESTIONS` list in `setup_metabase.py`
- **WHEN** it is inspected
- **THEN** `len(QUESTIONS) == 18` and `[q.number for q in QUESTIONS] == list(range(1, 19))`

#### Scenario: MBS-S10 DASHBOARDS has 3 entries with keys m7, m14e, fixer

- **GIVEN** the `DASHBOARDS` list in `setup_metabase.py`
- **WHEN** it is inspected
- **THEN** `len(DASHBOARDS) == 3` and `{d.key for d in DASHBOARDS} == {"m7", "m14e", "fixer"}`

#### Scenario: MBS-S11 every QuestionSpec SQL file exists on disk

- **GIVEN** the `QUESTIONS` list
- **WHEN** each `q.filename` is resolved against `observability/queries/sisyphus/`
- **THEN** the file exists and has non-zero size

#### Scenario: MBS-S12 cache_ttl values match the dashboard spec groups

- **GIVEN** the `QUESTIONS` list
- **WHEN** each `q.cache_ttl` is inspected
- **THEN** Q1 and Q5 have `cache_ttl == 30`
- **AND** Q2, Q3, Q4, Q12, Q13, Q17, Q18 have `cache_ttl == 120`
- **AND** Q6, Q7, Q8, Q9, Q10, Q11, Q14, Q15, Q16 have `cache_ttl == 1800`

#### Scenario: MBS-S13 main() returns exit code 1 when required args are missing

- **GIVEN** `setup_metabase.main()` is called with `--url` only
- **WHEN** required args (user, password, db-host, db-pass) are absent and `--dry-run` is not set
- **THEN** the function returns `1` without raising an exception

#### Scenario: MBS-S14 find_database_id matches by host and dbname

- **GIVEN** a list of databases including one with `host="pg-host"` and `dbname="sisyphus"`
- **WHEN** `find_database_id("pg-host", "sisyphus")` is called
- **THEN** the matching database id is returned
- **WHEN** `find_database_id("pg-host", "other")` or `find_database_id("other", "sisyphus")` is called
- **THEN** `None` is returned
