# Attendance Projections — XGBoost with Weather & Flight Data

Daily attendance forecasting for **PortAventura World** (or **Tivoli Gardens**) with an
interactive dashboard that treats the **last 30 operating days of the dataset as a live
forecast**: the model never sees them during training.

**Live dashboard:** deploys to Vercel as a static page — `public/index.html` (a synthetic demo build ships with the repo so the deployment works before any downloads).

## Data sources (all free, no keys)
| Source | What it provides |
|---|---|
| Kaggle [`ayushtankha/hackathon`](https://www.kaggle.com/datasets/ayushtankha/hackathon) | Daily attendance, hourly weather, ride wait times, schedules ("Disneyland" in the title, but the facilities are PortAventura World and Tivoli Gardens) |
| [EUROCONTROL AIU Airport Traffic](https://ansperformance.eu/data/) | Daily IFR arrivals/departures per airport, 2016–present (LEBL + LERS for PortAventura, EKCH for Tivoli) |

## Quickstart
```bash
pip install -r requirements.txt
python src/build_dashboard.py              # real data: downloads Kaggle + EUROCONTROL (cached)
python src/build_dashboard.py --synthetic  # offline demo build (no downloads)
open public/index.html
```
The shipped `public/index.html` is a **synthetic demo build** (badged as such in the
header) so the page works before any downloads. Re-run the build to replace it with
real results, then commit — Vercel redeploys automatically on push.

## The story the dashboard tells (top-down)
**Goal: predict attendance far enough ahead to set labor schedules and time ad
spend.** Two horizons are modeled: **week-ahead** (headline — only features
knowable 7+ days before the target, the schedule-lock / media-buy horizon) and
**day-ahead** (reference ceiling, uses yesterday's actuals). Panels in order:

1. **Decision KPIs** — typical miss at each horizon, systematic bias direction
   (under/overstaffing risk), soft days flagged a week out
2. **Forecast vs actual** — the evidence, with signed week-ahead error bars
3. **Staffing cost of error** — misallocated staff-hours and dollars from
   *your* ratio inputs (guests per staff-hour, loaded rate) — assumptions are
   explicit and adjustable, never fake precision
4. **Soft-day detection** — bottom-quartile days flagged 7 days out: caught,
   missed, false alarms (the ad-spend lens)
5. **Weekday bias + drivers** — recurring roster errors to correct; permutation
   importance of the deployable week-ahead model
6. **Diagnostics** — feature overlay explorer and error-vs-feature scatter, for
   understanding a miss before overriding the schedule
7. **Day-by-day ledger**

## Repository layout
```
notebooks/   full analysis notebook (EDA → features → tuning → leakage demos)
src/         pipeline.py (data + features) · build_dashboard.py · dashboard_template.html
public/      index.html (generated; Vercel serves this folder)
vercel.json  static deployment config (framework: Other, no build step)
```

## Deploy on Vercel
1. Push this repo to GitHub, then **vercel.com/new** → import the repo.
2. The checked-in `vercel.json` handles everything (Framework Preset **Other**, no
   build command, serve `public/`) — just click Deploy.
3. Done: the dashboard is live at `https://<project>.vercel.app/`.

CLI alternative from the repo root: `npx vercel --prod`.

**Refresh loop:** `python src/build_dashboard.py` → commit the regenerated
`public/index.html` → push → Vercel auto-redeploys. (Prefer GitHub Pages instead?
Build with `--out docs/dashboard.html` and point Pages at `/docs`.)

## Notes & honest caveats
- Flight counts are IFR movements (all instrument flights), a proxy for visitor
  inflow — not passenger counts. COVID collapses flights and attendance together, so
  flight-feature importance across 2020–21 deserves skepticism.
- `Curr_*`/`Next_*` flight windows are legitimate at prediction time because airline
  schedules publish months ahead; in deployment they'd come from schedules rather
  than EUROCONTROL actuals.
- The week-ahead model uses realized weather where production would use a 7-day
  forecast — treat its weather contribution as slightly flattering.
- The notebook includes a deliberate **leakage demonstration** (a fake "advance
  bookings" feature derived from the target) and a leakage-safe `Booked_AsOf_*`
  snapshot merge ready for real booking data.

## Possible next steps
- School-vacation calendars (Catalonia + feeder French zones, or Danish breaks) —
  the single highest-value missing feature
- Real advance-booking snapshots via `merge_booking_snapshots()` in the notebook
- A scheduled GitHub Action to rebuild monthly as EUROCONTROL publishes new data
  (requires Kaggle credentials as repo secrets)
