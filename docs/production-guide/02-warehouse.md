# Chapter 2 — The Warehouse

The warehouse sits between ingestion ([chapter 1](01-data-sources.md)) and the pipeline
([chapter 3](03-pipeline-and-model.md)). Its jobs: land every source immutably, replay
cleanly, and hand the training code a feature table whose columns match
`src/pipeline.py` byte-for-byte so `build_feature_frame()` logic ports without a rename
layer. This is small data; every choice below is about reliability and BI connectivity,
not scale.

## 2.1 PostgreSQL or DuckDB

| Dimension | PostgreSQL | DuckDB |
|---|---|---|
| Concurrency | Full MVCC: many writers + readers, fine for BI querying live while jobs load | One read-write process at a time (or many read-only, no writer). Fine when a single pipeline process owns the file |
| Power BI Desktop | Native connector, Import and DirectQuery | No first-party connector. Practical path: export marts to parquet, Import mode |
| Power BI Service refresh | Self-hosted / RDS PG needs the **on-premises data gateway**; Azure Database for PostgreSQL is a cloud source (no gateway) | Parquet files on OneDrive/SharePoint/ADLS refresh without a gateway |
| Ops overhead | A service: auth, backups, minor-version upgrades, connection limits | A file: backup is a copy, "install" is `pip install duckdb` |
| Scale needed here | Trivially sufficient | Trivially sufficient |
| Backup/DR | `pg_dump` nightly (seconds at this volume) | Copy the `.duckdb` file + parquet exports |

**Recommendation.** Pick **PostgreSQL** when Power BI (or anything else) queries the
warehouse directly, or when more than one process writes — e.g. ticketing lands via a
vendor push while your scheduler runs ingest jobs. Pick **DuckDB** when one scheduled
pipeline process owns the database file and Power BI consumes parquet extracts in Import
mode. For a 1–3 person team with a single nightly DAG, DuckDB is genuinely less to run;
the moment a second writer or a DirectQuery requirement appears, use Postgres. Both
choices use the same DDL below (dialect deltas are flagged inline) and the same
`INSERT ... ON CONFLICT` load pattern.

## 2.2 Layers

| Layer | Contents | Rules |
|---|---|---|
| `raw` | One table per source feed, landed as received | **Append-only, immutable.** Never updated, never deleted (except GDPR erasure). Every table carries `source`, `_batch_id`, `_loaded_at` |
| `staging` | Views over `raw`: typed, deduped, operating-day derived | **No storage, no state.** Re-running anything downstream re-reads these; a raw replay automatically flows through |
| `marts` | `features_daily`, `forecasts`, `forecast_accuracy` | Physical tables. `features_daily` is upserted; `forecasts` is **append-only history** (README ground rule 4) |

Snapshot tables (`booking_snapshots`, `weather_forecast_snapshots`,
`flight_schedule_snapshots`) are the production fix for the PoC's two flattering
shortcuts: the PoC trains on **realized** weather and uses EUROCONTROL **actuals** as a
stand-in for future flight windows (`Curr_*`/`Next_*` in
`src/pipeline.py::build_flight_window_features`). In production, anything about the
future is stored keyed by *the date the statement was made*, so backtests replay exactly
what was knowable (README ground rule 2).

## 2.3 DDL

Portable across PostgreSQL 15+ and DuckDB 1.x unless a comment says otherwise. Feature
columns are double-quoted to preserve the exact case `src/pipeline.py` produces
(unquoted, Postgres would fold them to lowercase).

### Reference and raw

```sql
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS marts;

CREATE TABLE IF NOT EXISTS ref.parks (
  park_id        TEXT PRIMARY KEY,          -- 'PAW', 'USP01'
  park_name      TEXT NOT NULL,
  park_tz        TEXT NOT NULL,             -- 'Europe/Madrid' | 'America/New_York'
  day_cutoff_hr  SMALLINT NOT NULL DEFAULT 4,  -- post-midnight boundary, see 2.5
  airports       TEXT NOT NULL              -- csv of codes: 'LEBL,LERS' | 'MCO'
);

-- Ticketing platform export. Land at the finest grain the platform gives you:
-- per-scan if available, else per (day x ticket category). UTC always.
CREATE TABLE IF NOT EXISTS raw.admissions (
  park_id         TEXT        NOT NULL,
  event_ts_utc    TIMESTAMPTZ NOT NULL,     -- both engines store TIMESTAMPTZ as a UTC instant
  ticket_type     TEXT        NOT NULL DEFAULT 'all',
  sales_channel   TEXT        NOT NULL DEFAULT 'all',
  entries         INTEGER     NOT NULL,
  source          TEXT        NOT NULL,     -- 'ticketing:<vendor>'
  source_file     TEXT,
  _batch_id       TEXT        NOT NULL,
  _loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (park_id, event_ts_utc, ticket_type, sales_channel, _batch_id)
);

-- Advance bookings on the books, snapshotted daily. The single highest-value
-- production feature; feeds the Booked_* hook in pipeline.feature_group().
CREATE TABLE IF NOT EXISTS raw.booking_snapshots (
  park_id       TEXT NOT NULL,
  snapshot_date DATE NOT NULL,              -- park-local date the snapshot was taken
  target_date   DATE NOT NULL,              -- visit date being booked
  channel       TEXT NOT NULL DEFAULT 'all',
  bookings      INTEGER NOT NULL,
  source        TEXT NOT NULL,
  _batch_id     TEXT NOT NULL,
  _loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (park_id, snapshot_date, target_date, channel, _batch_id)
);

-- Flight ACTUALS: trailing features and backfill only — published with lag.
-- EU: EUROCONTROL airport_traffic CSVs (verify data-license terms for commercial
--     use before deploying; the PoC uses them under research-style access).
-- US: FAA ASPM/OPSNET operations counts; BTS T-100 segments (~2-month lag, so
--     T-100 is backfill-grade only). TSA checkpoint throughput (public domain)
--     is NATIONAL daily passenger totals, not per-airport — a demand proxy; if
--     used, land it in a sibling raw.tsa_throughput with this lineage pattern.
CREATE TABLE IF NOT EXISTS raw.flight_actuals (
  airport_code TEXT NOT NULL,               -- ICAO ('LEBL') or FAA/IATA ('MCO'); pick one, note in source
  flight_date  DATE NOT NULL,               -- airport-local date as published
  arrivals     INTEGER,
  departures   INTEGER,
  source       TEXT NOT NULL,               -- 'eurocontrol_apt'|'faa_aspm'|'faa_opsnet'|'bts_t100'
  _batch_id    TEXT NOT NULL,
  _loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (airport_code, flight_date, source, _batch_id)
);

-- Flight SCHEDULES for future windows (Curr_*/Next_* features). Commercial
-- vendors in BOTH regions (OAG, Cirium — paid licenses); no free public feed
-- publishes forward schedules. Snapshotted so backtests replay what the
-- schedule said, not what later flew.
CREATE TABLE IF NOT EXISTS raw.flight_schedule_snapshots (
  snapshot_date        DATE NOT NULL,       -- when this schedule statement was pulled
  airport_code         TEXT NOT NULL,
  flight_date          DATE NOT NULL,
  scheduled_arrivals   INTEGER,
  scheduled_departures INTEGER,
  scheduled_seats_arr  INTEGER,             -- if licensed; seats beat movements
  source               TEXT NOT NULL,       -- 'oag'|'cirium'
  _batch_id            TEXT NOT NULL,
  _loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_date, airport_code, flight_date, source, _batch_id)
);

-- Observed weather, hourly grain as delivered; staging aggregates to the
-- park-local day per pipeline.WEATHER_AGG.
-- EU: Open-Meteo archive (free tier is NON-commercial; buy a commercial plan
--     for a park company) or the national met service (e.g. AEMET OpenData).
-- US: NWS api.weather.gov observations — US-government public domain, free
--     including commercial use.
CREATE TABLE IF NOT EXISTS raw.weather_history (
  station_id    TEXT NOT NULL,
  obs_ts_utc    TIMESTAMPTZ NOT NULL,
  temp_c        DOUBLE PRECISION,           -- DuckDB reads DOUBLE PRECISION as its DOUBLE
  temp_min_c    DOUBLE PRECISION,
  temp_max_c    DOUBLE PRECISION,
  humidity_pct  DOUBLE PRECISION,
  rain_1h_mm    DOUBLE PRECISION,
  wind_speed_ms DOUBLE PRECISION,
  clouds_pct    DOUBLE PRECISION,
  source        TEXT NOT NULL,              -- 'open_meteo_era5'|'aemet'|'nws_obs'|'noaa_ghcn'
  _batch_id     TEXT NOT NULL,
  _loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (station_id, obs_ts_utc, source, _batch_id)
);

-- Forecast-as-of snapshots: the production fix for the PoC's realized-weather
-- shortcut. One row per (snapshot day, target day); the week-ahead model reads
-- the snapshot taken >= 7 days before target_date, never a fresher one.
CREATE TABLE IF NOT EXISTS raw.weather_forecast_snapshots (
  station_id    TEXT NOT NULL,
  snapshot_date DATE NOT NULL,
  target_date   DATE NOT NULL,
  temp_max_c    DOUBLE PRECISION,
  temp_min_c    DOUBLE PRECISION,
  precip_mm     DOUBLE PRECISION,
  wind_speed_ms DOUBLE PRECISION,
  clouds_pct    DOUBLE PRECISION,
  humidity_pct  DOUBLE PRECISION,
  source        TEXT NOT NULL,              -- 'open_meteo_fcst'|'nws_gridpoint'
  _batch_id     TEXT NOT NULL,
  _loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (station_id, snapshot_date, target_date, source, _batch_id)
);

-- Generated by the `holidays` pip package + school calendars entered by hand.
-- EU example: country 'ES', subdivision 'CT' (matches PARK_HOLIDAYS in
-- src/pipeline.py). US example: 'US' federal plus subdivision 'FL' — state
-- observances matter for regional visitation.
CREATE TABLE IF NOT EXISTS raw.holiday_calendar (
  country_code      TEXT NOT NULL,
  subdivision       TEXT NOT NULL DEFAULT '',
  holiday_date      DATE NOT NULL,
  holiday_name      TEXT NOT NULL,
  is_school_holiday BOOLEAN NOT NULL DEFAULT false,
  source            TEXT NOT NULL,          -- 'python-holidays==0.x'|'manual:school'
  _batch_id         TEXT NOT NULL,
  _loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (country_code, subdivision, holiday_date, holiday_name, _batch_id)
);
```

### Staging

Staging is views only. Each one: keep the newest batch per natural key, cast types,
derive `operating_date`. Full text for admissions; the others follow the same shape.

```sql
CREATE OR REPLACE VIEW staging.admissions AS
WITH ranked AS (
  SELECT a.*,
         ROW_NUMBER() OVER (
           PARTITION BY a.park_id, a.event_ts_utc, a.ticket_type, a.sales_channel
           ORDER BY a._loaded_at DESC) AS rn      -- newest batch wins on replay
  FROM raw.admissions a
)
SELECT r.park_id,
       r.event_ts_utc,
       -- park-local wall time, minus the post-midnight cutoff, cast to date.
       -- timezone(zone, timestamptz) works in PG natively and in DuckDB via the
       -- ICU extension (bundled in official builds). Integer x interval
       -- multiplication is portable across both engines. See 2.5.
       CAST(timezone(p.park_tz, r.event_ts_utc)
            - p.day_cutoff_hr * INTERVAL '1 hour' AS DATE) AS operating_date,
       r.ticket_type,
       r.sales_channel,
       r.entries
FROM ranked r
JOIN ref.parks p USING (park_id)
WHERE r.rn = 1;
```

The rest, described once each:

| View | From | Adds |
|---|---|---|
| `staging.attendance_daily` | `staging.admissions` | `SUM(entries)` per `park_id, operating_date` — the `Entries` target |
| `staging.weather_daily` | `raw.weather_history` | Newest batch, hourly → park-local-day aggregates named per `pipeline.WEATHER_AGG`: `Avg_Temp_C` (mean), `Max_Temp_C` (max), `Min_Temp_C` (min), `Avg_Humidity_pct` (mean), `Total_Rain_mm` (sum), `Avg_Wind_ms` (mean), `Avg_Clouds_pct` (mean) |
| `staging.weather_asof_7d` | `raw.weather_forecast_snapshots` | Per `target_date`, the newest snapshot with `snapshot_date <= target_date - 7` — the only weather the week-ahead model may see |
| `staging.flights_daily` | `raw.flight_actuals` + `raw.flight_schedule_snapshots` | One `arrivals`/`departures` series per park: actuals for past dates, the freshest *eligible* schedule snapshot for future dates; summed over the park's airports (cf. `PARK_AIRPORTS`) |
| `staging.bookings_asof_7d` | `raw.booking_snapshots` | On-books totals per `target_date` as of 7 days out |
| `staging.holidays` | `raw.holiday_calendar` | Deduped union of public + school days per park's country/subdivision |

Window math on `staging.flights_daily` (the `Last/Curr/Next x Day/Week/Month` totals)
stays in Python — `build_flight_window_features()` in `src/pipeline.py` is unit-tested;
don't re-implement it in SQL.

### Marts

`marts.features_daily`: one row per `park_id x operating_date`, columns exactly the
frame `build_feature_frame()` produces, so training and `WEEK_AHEAD_DROP` filtering in
`src/build_dashboard.py` work unchanged. `Date_Num` is derived at train time, not
stored.

```sql
CREATE TABLE IF NOT EXISTS marts.features_daily (
  park_id                 TEXT NOT NULL,
  operating_date          DATE NOT NULL,
  "Entries"               DOUBLE PRECISION,   -- actual; NULL until actuals land
  -- calendar (src/pipeline.py::add_calendar_features / add_pack2_features)
  "DayOfWeek"             SMALLINT,           -- 0=Mon..6=Sun, pandas convention:
                                              -- EXTRACT(isodow FROM d)-1 in both engines
  "Is_Weekend"            SMALLINT, "Month" SMALLINT, "DayOfMonth" SMALLINT,
  "Is_Holiday"            SMALLINT,
  "DayOfYear_Sin"  DOUBLE PRECISION, "DayOfYear_Cos"  DOUBLE PRECISION,
  "DayOfYear_Sin2" DOUBLE PRECISION, "DayOfYear_Cos2" DOUBLE PRECISION,
  "Days_To_Holiday"       SMALLINT, "Days_Since_Holiday" SMALLINT,
  "Is_Bridge_Day"         SMALLINT, "Is_Easter_Week" SMALLINT,
  "Is_Xmas_Period"        SMALLINT, "Is_Summer_Peak" SMALLINT,
  -- weather (WEATHER_AGG names; from as-of snapshots for future dates)
  "Avg_Temp_C"       DOUBLE PRECISION, "Max_Temp_C" DOUBLE PRECISION,
  "Min_Temp_C"       DOUBLE PRECISION, "Avg_Humidity_pct" DOUBLE PRECISION,
  "Total_Rain_mm"    DOUBLE PRECISION, "Avg_Wind_ms" DOUBLE PRECISION,
  "Avg_Clouds_pct"   DOUBLE PRECISION,
  -- flights: Arr_/Dep_ x Last/Curr/Next x Day/Week/Month + ratio features
  "Arr_Last_Day"  DOUBLE PRECISION, "Arr_Curr_Day"  DOUBLE PRECISION,
  "Arr_Next_Day"  DOUBLE PRECISION, "Arr_Last_Week" DOUBLE PRECISION,
  "Arr_Curr_Week" DOUBLE PRECISION, "Arr_Next_Week" DOUBLE PRECISION,
  "Arr_Last_Month" DOUBLE PRECISION, "Arr_Curr_Month" DOUBLE PRECISION,
  "Arr_Next_Month" DOUBLE PRECISION,
  "Dep_Last_Day"  DOUBLE PRECISION, "Dep_Curr_Day"  DOUBLE PRECISION,
  "Dep_Next_Day"  DOUBLE PRECISION, "Dep_Last_Week" DOUBLE PRECISION,
  "Dep_Curr_Week" DOUBLE PRECISION, "Dep_Next_Week" DOUBLE PRECISION,
  "Dep_Last_Month" DOUBLE PRECISION, "Dep_Curr_Month" DOUBLE PRECISION,
  "Dep_Next_Month" DOUBLE PRECISION,
  "Arr_Week_Momentum" DOUBLE PRECISION, "Dep_Week_Momentum" DOUBLE PRECISION,
  "Arr_Curr_vs_Trail" DOUBLE PRECISION, "Dep_Curr_vs_Trail" DOUBLE PRECISION,
  -- attendance lags incl. week-safe variants
  "Entries_Lag_1"   DOUBLE PRECISION, "Entries_Lag_7"   DOUBLE PRECISION,
  "Entries_Lag_14"  DOUBLE PRECISION, "Entries_Lag_21"  DOUBLE PRECISION,
  "Entries_Lag_28"  DOUBLE PRECISION, "Entries_Lag_364" DOUBLE PRECISION,
  "Entries_Roll_Mean_7"    DOUBLE PRECISION, "Entries_Roll_Std_7" DOUBLE PRECISION,
  "Entries_SameDOW_Mean4"  DOUBLE PRECISION, "Entries_WoW_Diff"   DOUBLE PRECISION,
  "Entries_WkSafe_Mean7"   DOUBLE PRECISION, "Entries_WkSafe_Std7" DOUBLE PRECISION,
  "Entries_Lag7_minus_14"  DOUBLE PRECISION,
  -- operations
  "Open_Hours" DOUBLE PRECISION,
  "Wait_Mean_Lag_1" DOUBLE PRECISION, "Wait_Mean_Lag_7" DOUBLE PRECISION,
  -- bookings: keep the Booked_ prefix so pipeline.feature_group() buckets them
  "Booked_AsOf_7d" DOUBLE PRECISION, "Booked_AsOf_1d" DOUBLE PRECISION,
  "Booked_vs_LY_Ratio" DOUBLE PRECISION,
  _batch_id  TEXT NOT NULL,
  _built_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (park_id, operating_date)
);

-- Append-only forecast history (README ground rule 4): one row per run per
-- target day; you can always answer "what did we tell ops last Tuesday?".
-- p10/p90 = the PoC's 80% band from out-of-fold ratio quantiles.
CREATE TABLE IF NOT EXISTS marts.forecasts (
  park_id       TEXT NOT NULL,
  target_date   DATE NOT NULL,
  run_date      DATE NOT NULL,               -- park-local date of the run, set by the job
  run_ts        TIMESTAMPTZ NOT NULL,
  model_version TEXT NOT NULL,               -- registry tag, e.g. '2026-08-17_w1_a1b2c3d' (ch. 3)
  horizon_days  SMALLINT NOT NULL,           -- target_date - run_date
  p10 DOUBLE PRECISION,                      -- NULL on fallback rows (no band)
  p50 DOUBLE PRECISION NOT NULL,
  p90 DOUBLE PRECISION,                      -- NULL on fallback rows (no band)
  is_fallback   BOOLEAN NOT NULL DEFAULT false,  -- ch. 5 fallback policy
  PRIMARY KEY (park_id, target_date, run_date)   -- natural key
);
CREATE INDEX IF NOT EXISTS ix_forecasts_run
  ON marts.forecasts (park_id, run_date);        -- "latest run" queries; PBI extract

-- Scored once actuals land, for EVERY horizon 1-14 the service wrote; the
-- Power BI report defaults its filters to horizons 1 (day-ahead reference)
-- and 7 (the week-ahead decision horizon).
CREATE TABLE IF NOT EXISTS marts.forecast_accuracy (
  park_id        TEXT NOT NULL,
  target_date    DATE NOT NULL,
  horizon_days   SMALLINT NOT NULL,
  model_version  TEXT NOT NULL,
  is_fallback    BOOLEAN NOT NULL,
  p50            DOUBLE PRECISION NOT NULL,
  actual_entries DOUBLE PRECISION NOT NULL,
  abs_error      DOUBLE PRECISION NOT NULL,
  pct_error      DOUBLE PRECISION,
  in_band        BOOLEAN NOT NULL,           -- p10 <= actual <= p90; feeds coverage KPI
  scored_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (park_id, target_date, horizon_days)
);
```

## 2.4 Idempotency and backfill

Both engines support `INSERT ... ON CONFLICT`, which is the entire load protocol
(README ground rule 3). Raw loads never update:

```sql
-- raw: re-landing the same batch is a no-op; a *new* batch appends and staging's
-- ROW_NUMBER() dedupe makes the newest batch win downstream.
INSERT INTO raw.admissions (park_id, event_ts_utc, ticket_type, sales_channel,
                            entries, source, source_file, _batch_id)
VALUES (...)
ON CONFLICT (park_id, event_ts_utc, ticket_type, sales_channel, _batch_id)
DO NOTHING;

-- marts: upsert on the natural key so any date range can be rebuilt freely.
INSERT INTO marts.features_daily AS f (park_id, operating_date, "Entries",
                                       "DayOfWeek", /* ... */ _batch_id)
VALUES (...)
ON CONFLICT (park_id, operating_date) DO UPDATE
  SET "Entries"  = EXCLUDED."Entries",
      "DayOfWeek" = EXCLUDED."DayOfWeek",  -- ...every non-key column
      _batch_id  = EXCLUDED._batch_id,
      _built_at  = now();
-- Dialect note: PG allows the `AS f` alias on the target; DuckDB does not need
-- it and EXCLUDED works identically in both.
```

`marts.forecasts` uses the same upsert on `(park_id, target_date, run_date)`: rerunning
*today's* failed job overwrites today's rows (idempotent), while yesterday's rows are
untouched (append-only across days). Never `DELETE` from it; a bad historical run is
marked in the model registry (ch. 3), not erased.

**Backfill any date range** — same jobs, bounded parameters, no special code path:

1. `ingest --from 2026-05-01 --to 2026-05-31` per source. Raw PKs make re-landing safe;
   snapshot tables backfill from vendor archives where they exist, and stay honestly
   empty where they don't (a gap in `weather_forecast_snapshots` is a gap — do not
   substitute realized weather, that recreates the PoC shortcut).
2. Nothing to run for staging — views recompute on read.
3. `build_features --park PAW --from ... --to ...` upserts `marts.features_daily`.
   Looking *backward*, nothing before the corrected range needs rebuilding — lags read
   prior actuals from `staging.attendance_daily` at build time. Looking *forward* is the
   trap: rows up to 364 days AFTER a corrected actual read it through `Entries_Lag_364`
   (and every shorter lag). The cheap, correct rule for this data size: rebuild features
   **from the start of the corrected range forward to today**.
4. `score_accuracy --from ... --to ...` re-joins `marts.forecasts` to actuals and
   upserts `marts.forecast_accuracy`.

## 2.5 Timezones and the operating day

Rules, in priority order:

1. **Every event timestamp is stored UTC** (`TIMESTAMPTZ`). Local wall time is derived,
   never stored as the primary fact.
2. **`operating_date` is the park-local calendar date after subtracting the cutoff**
   (default 04:00, from `ref.parks.day_cutoff_hr`):
   `CAST(timezone(park_tz, event_ts_utc) - day_cutoff_hr * INTERVAL '1 hour' AS DATE)`.
   A gate scan during a midnight-fireworks close at 00:40 local belongs to the *previous*
   operating day — the day the staffing decision was made for.
3. **Weather aggregates over the park-local day**, not the UTC day. The PoC normalizes
   tz-aware hourly stamps to UTC dates (`src/pipeline.py::add_weather_features`,
   `tz_localize(None).normalize()`) — a known 1–2 hour smear the warehouse fixes for
   free once `staging.weather_daily` groups by `operating_date`.
4. **Flights join on `operating_date` directly**: EUROCONTROL `FLT_DATE` and US
   ASPM/OPSNET dates are already airport-local calendar dates; no timestamp math.

Worked examples at the 2026 DST transitions:

| Event (UTC) | Park tz | Local wall time | operating_date |
|---|---|---|---|
| `2026-03-28 23:40Z` | Europe/Madrid | 00:40 CET Mar 29 | **Mar 28** (post-midnight cutoff) |
| `2026-03-29 08:00Z` | Europe/Madrid | 10:00 CEST (spring-forward night: Mar 29 has 23 local hours) | Mar 29 |
| `2026-10-25 06:30Z` | Europe/Madrid | 07:30 CET (fall-back: Oct 25 has 25 local hours) | Oct 25 |
| `2026-03-08 12:00Z` | America/New_York | 08:00 EDT (spring-forward day) | Mar 8 |
| `2026-11-01 06:30Z` | America/New_York | 01:30 EST — 01:30 occurs **twice** that night; UTC storage disambiguates | **Oct 31** (cutoff) |

Consequences worth writing tests for: a 23-hour local day has 23 hourly weather rows
(`Total_Rain_mm` sums fewer readings — correct, not a data-quality failure); `Open_Hours`
computed from local open/close spans is unaffected because both ends shift together; and
DST rules differ between the regions (EU changes late March / late October, US second
Sunday of March / first Sunday of November), so never hard-code offsets — always the IANA
zone name from `ref.parks`. DuckDB needs the ICU extension for `timezone()`; it ships in
official builds, but pin it in the pipeline image and smoke-test at startup.

Reconcile `SUM(entries) GROUP BY operating_date` against the ticketing platform's own
daily report during rollout (ch. 5 quality gates) — if the vendor uses a different
business-day boundary, adopt theirs via `ref.parks.day_cutoff_hr` rather than running two
definitions.

## 2.6 Retention and size honesty

This warehouse is small and will stay small. Per park, per year, roughly:

| Table | Rows/year | Notes |
|---|---|---|
| `raw.admissions` (daily x category grain) | ~2–4 k | Per-scan grain: a few million — still trivial for either engine |
| `raw.weather_history` (hourly) | ~9 k | |
| `raw.weather_forecast_snapshots` | ~5 k | 365 snapshots × 14 target days (D+1..D+14) |
| `raw.flight_schedule_snapshots` | ~4 k | 52 weekly snapshots × ~35 fwd days × 2 airports |
| `raw.flight_actuals`, `raw.booking_snapshots`, `raw.holiday_calendar` | ~15 k combined | |
| `marts.features_daily` (~75 cols) | 365 | |
| `marts.forecasts` | ~5 k | 365 runs × 14 horizons |

Call it **single-digit MB per park-year**. Therefore: **no partitioning** — it buys
nothing at this size and complicates both engines; no column store tuning; no retention
deletes — keep everything forever, because same-season prior-year folds (the PoC's band
pooling in `src/build_dashboard.py`) and `Entries_Lag_364` get *more* valuable with
depth, and five parks × ten years still fits in memory on a laptop. The only "retention"
task is exporting parquet extracts of the three marts for Power BI Import mode
([chapter 4](04-powerbi.md)) and including raw in the nightly backup. If anyone proposes
a distributed warehouse for this workload, show them this table.
