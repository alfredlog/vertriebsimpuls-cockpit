# ADR 0001: DuckDB as the default database, BigQuery as an optional backend

- Status: accepted
- Date: 2026-11-27
- Code: `src/vic/db.py`, `src/vic/bq.py`, `docs/bigquery.md`

## Context

The project needs a database for three things: the daily pipeline (load, signals,
ranking), the Dash cockpit (reads and feedback writes), and the test suite. It is a
portfolio project: anyone must be able to clone it and run everything offline in
minutes, without a cloud account or costs. At the same time, the target environment
in a bank's data team is typically a cloud data warehouse, and BigQuery is a
common choice.

## Decision

- **DuckDB is the default and the only database the pipeline, the app and the
  tests use.** One file (`data/vic.duckdb`), or `":memory:"` in tests.
- **BigQuery is optional** (`uv sync --group bigquery`). It loads the same star
  schema from the same generated DataFrames and shows three example queries with
  their cost behaviour. Nothing else depends on it; the default test run does not
  import it.
- Authentication only through Application Default Credentials. No key files.
- Loading uses **load jobs with WRITE_TRUNCATE**, never DML or streaming, because
  the BigQuery sandbox supports neither.
- `fact_trades` is partitioned by month on `trade_date` and clustered by
  `client_id`. In **sandbox mode** it is not partitioned: sandbox partitions expire
  60 days after their partition date, and older partitions expire on load, which
  would drop most of our two-year history. It is clustered by
  `trade_date, client_id` instead.

## Why DuckDB by default

1. **Zero setup and zero cost:** an embedded library, no server, no account.
2. **Fast tests:** every test builds its own in-memory database in milliseconds,
   with hand-made fixtures.
3. **Enforced constraints:** primary keys, foreign keys, CHECK constraints and ENUM
   types catch bad data at load time; BigQuery constraints are `NOT ENFORCED`.
4. **Transactions:** the daily reload runs in one transaction and rolls back
   completely on error. BigQuery has no cross-table transaction without DML.
5. **Size:** the dataset is a few MB. A warehouse brings no benefit at this size;
   BigQuery's 10 MB minimum per table and query would dominate the cost.

## Why still BigQuery

- It shows the production path: the same model in a serverless warehouse, with
  the design choices that matter there (partitioning, clustering, column selection,
  dry runs) and the dialect differences found in this project.
- Cost awareness is part of the design: bytes processed are estimated and
  measurable (`vic.bq estimate` / `dry-run`).

## Consequences

- SQL exists in two dialects. The BigQuery queries are separate files
  (`src/vic/sql/bigquery/`), not translated automatically. `docs/bigquery.md`
  lists the differences we met (DATE_DIFF argument order, DATE_TRUNC, parameters,
  ENUM, NUMERIC, constraints).
- The BigQuery path is tested with a mocked client only. Real loads and dry runs
  need a Google Cloud project and are run by hand; the byte figures in the docs are
  calculated from BigQuery's data type sizes until a dry run has been executed.
- If the data ever outgrows a single machine, the pipeline would have to write to
  BigQuery directly; `vic.db` is the single place where that switch would happen.

## Alternatives considered

- **BigQuery only:** realistic, but every clone needs a cloud project, tests need
  network or emulators, and the sandbox limits (no DML, 60-day expiry) clash with
  daily reloads and feedback writes.
- **SQLite:** no analytical SQL features we use (QUALIFY, window-heavy views,
  `arg_max`), weaker typing.
- **PostgreSQL in Docker:** realistic OLTP, but extra setup for little analytical
  benefit at this size.
- **SQLGlot to translate one SQL source into both dialects:** attractive, but an
  extra dependency and a source of subtle bugs (e.g. DATE_DIFF order); with three
  example queries, explicit files are clearer.
