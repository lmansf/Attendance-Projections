"""Data + feature pipeline for the theme-park attendance forecast.

Ports the (tested) logic from the analysis notebook into importable functions:
Kaggle attendance/weather loaders, EUROCONTROL flight-volume features,
calendar/holiday features, attendance lags, and the extended feature pack.
Also ships synthetic generators so the whole pipeline can run offline
(`--synthetic` in build_dashboard.py).
"""
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
PARK_AIRPORTS = {
    "PortAventura World": ["LEBL", "LERS"],   # Barcelona El Prat + Reus
    "Tivoli Gardens": ["EKCH"],               # Copenhagen Kastrup
}
PARK_HOLIDAYS = {
    "PortAventura World": ("ES", "CT"),       # Spain / Catalonia
    "Tivoli Gardens": ("DK", None),           # Denmark
}
EUROCONTROL_CSV = ("https://www.eurocontrol.int/performance/data/download/csv/"
                   "airport_traffic_{year}.csv")
RANDOM_STATE = 42

WEATHER_AGG = {  # source column -> (output name, aggregation)
    "temp": ("Avg_Temp_C", "mean"),
    "temp_max": ("Max_Temp_C", "max"),
    "temp_min": ("Min_Temp_C", "min"),
    "humidity": ("Avg_Humidity_pct", "mean"),
    "rain_1h": ("Total_Rain_mm", "sum"),
    "wind_speed": ("Avg_Wind_ms", "mean"),
    "clouds_all": ("Avg_Clouds_pct", "mean"),
}


def feature_group(col: str) -> str:
    """Bucket a feature column for dashboard grouping."""
    if col.startswith(("Arr_", "Dep_")):
        return "Flights"
    if col in {v[0] for v in WEATHER_AGG.values()}:
        return "Weather"
    if col.startswith("Entries_"):
        return "Attendance lags"
    if col in ("Open_Hours", "Wait_Mean_Lag_1", "Wait_Mean_Lag_7"):
        return "Operations"
    if col.startswith("Booked_"):
        return "Bookings"
    return "Calendar"


# --------------------------------------------------------------------------
# Kaggle files
# --------------------------------------------------------------------------
def download_kaggle_files(slug: str = "ayushtankha/hackathon") -> list[Path]:
    """Download the Kaggle dataset (network required) and list its files."""
    import kagglehub  # lazy: not needed in synthetic mode
    path = Path(kagglehub.dataset_download(slug))
    return sorted(p for p in path.rglob("*") if p.is_file())


def _find_file(files, key, suffix=".csv"):
    return next((p for p in files
                 if key in p.name.lower() and p.suffix.lower() == suffix), None)


def load_attendance(files, park: str) -> pd.DataFrame:
    """Daily attendance for one park -> DataFrame[Date, Entries]."""
    fp = _find_file(files, "attendance")
    if fp is None:
        raise FileNotFoundError("no attendance file in the Kaggle download")
    raw = pd.read_csv(fp, sep=None, engine="python")
    raw.columns = [c.strip() for c in raw.columns]
    low = {c.lower(): c for c in raw.columns}
    date_col = next((low[c] for c in ("usage_date", "date", "work_date", "day")
                     if c in low), None)
    park_col = next((low[c] for c in ("facility_name", "park", "facility", "site")
                     if c in low), None)
    target_col = next((low[c] for c in ("attendance", "entries", "visitors", "guests")
                       if c in low), None)
    if not (date_col and target_col):
        raise ValueError(f"unrecognized attendance columns: {list(raw.columns)}")

    df = raw.copy()
    if park_col:
        df = df[df[park_col] == park]
    df = df[[date_col, target_col]].rename(columns={date_col: "Date",
                                                    target_col: "Entries"})
    df["Date"] = pd.to_datetime(df["Date"])
    df["Entries"] = pd.to_numeric(df["Entries"], errors="coerce")
    df = df.dropna(subset=["Entries"])
    df = df[df["Entries"] > 0]
    return (df.groupby("Date", as_index=False)["Entries"].sum()
              .sort_values("Date").reset_index(drop=True))


def add_weather_features(df: pd.DataFrame, files) -> tuple[pd.DataFrame, list[str]]:
    fp = _find_file(files, "weather")
    if fp is None:
        return df, []
    wx = pd.read_csv(fp, sep=None, engine="python")
    wx.columns = [c.strip() for c in wx.columns]
    low = {c.lower(): c for c in wx.columns}
    ts_col = next((low[c] for c in ("dt_iso", "datetime", "date_time", "date", "dt")
                   if c in low), None)
    if ts_col is None:
        return df, []
    ts = pd.to_datetime(wx[ts_col], errors="coerce", utc=True)
    if ts.isna().mean() > 0.5:
        ts = pd.to_datetime(wx[ts_col].astype(str).str.slice(0, 19),
                            errors="coerce", utc=True)
    wx["Date"] = ts.dt.tz_localize(None).dt.normalize()

    plan = {}
    for src, (out, how) in WEATHER_AGG.items():
        if src in low:
            col = low[src]
            wx[col] = pd.to_numeric(wx[col], errors="coerce")
            if how == "sum":
                wx[col] = wx[col].fillna(0)
            plan[col] = (out, how)
    daily = wx.groupby("Date").agg({c: how for c, (out, how) in plan.items()})
    daily.columns = [plan[c][0] for c in daily.columns]
    daily = daily.round(2).reset_index()
    cols = [c for c in daily.columns if c != "Date"]
    return df.merge(daily, on="Date", how="left"), cols


# --------------------------------------------------------------------------
# Calendar / holidays
# --------------------------------------------------------------------------
def _holiday_calendar(park: str, years):
    try:
        import holidays as holidays_lib
        country, subdiv = PARK_HOLIDAYS[park]
        return holidays_lib.country_holidays(country, subdiv=subdiv,
                                             years=list(years))
    except ImportError:
        print("WARNING: `holidays` not installed - holiday features set to 0.")
        return None


def add_calendar_features(df: pd.DataFrame, park: str) -> pd.DataFrame:
    df = df.copy()
    years = range(df["Date"].dt.year.min() - 1, df["Date"].dt.year.max() + 2)
    hol = _holiday_calendar(park, years)
    df["DayOfWeek"] = df["Date"].dt.dayofweek
    df["Is_Weekend"] = df["DayOfWeek"].isin([5, 6]).astype(int)
    df["Month"] = df["Date"].dt.month
    df["DayOfMonth"] = df["Date"].dt.day
    df["Is_Holiday"] = (df["Date"].apply(lambda d: int(d in hol))
                        if hol is not None else 0)
    doy = df["Date"].dt.dayofyear
    df["DayOfYear_Sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["DayOfYear_Cos"] = np.cos(2 * np.pi * doy / 365.25)
    return df


# --------------------------------------------------------------------------
# EUROCONTROL flight volumes
# --------------------------------------------------------------------------
def fetch_airport_traffic_year(year: int, cache_dir: Path, session=None,
                               retries: int = 3):
    """One year of EUROCONTROL airport traffic (cached). None if unpublished."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw_file = cache_dir / f"airport_traffic_{year}.csv"
    if not raw_file.exists():
        import requests
        session = session or requests.Session()
        url = EUROCONTROL_CSV.format(year=year)
        last_err = None
        for attempt in range(retries):
            try:
                resp = session.get(url, timeout=180)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                raw_file.write_bytes(resp.content)
                break
            except Exception as e:  # noqa: BLE001 - retry any transport error
                last_err = e
                time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"could not download {url}: {last_err}")
    return pd.read_csv(raw_file)


def daily_airport_counts(years, airports, cache_dir: Path) -> pd.DataFrame:
    frames = []
    for y in years:
        raw = fetch_airport_traffic_year(y, cache_dir)
        if raw is None:
            continue
        raw.columns = [c.strip().upper() for c in raw.columns]
        part = raw[raw["APT_ICAO"].isin(airports)].copy()
        part["Date"] = pd.to_datetime(part["FLT_DATE"].astype(str).str.strip(),
                                      errors="coerce")
        part = (part.groupby("Date")[["FLT_ARR_1", "FLT_DEP_1"]].sum()
                    .rename(columns={"FLT_ARR_1": "Arrivals",
                                     "FLT_DEP_1": "Departures"}))
        frames.append(part)
    if not frames:
        raise RuntimeError("no EUROCONTROL data available for requested years")
    return pd.concat(frames).sort_index().reset_index()


def build_flight_window_features(flight_daily: pd.DataFrame,
                                 prefix_map=(("Arrivals", "Arr"),
                                             ("Departures", "Dep"))) -> pd.DataFrame:
    """last / current / next  x  day / week / month totals (unit-tested logic).

    Leading windows use: sum over [t+1, t+w] == trailing w-day rolling sum at
    t+w, shifted back to t.
    """
    fd = flight_daily.copy()
    fd["Date"] = pd.to_datetime(fd["Date"])
    fd = fd.sort_values("Date").set_index("Date")
    fd = fd.reindex(pd.date_range(fd.index.min(), fd.index.max(), freq="D"))
    fd.index.name = "Date"

    out = pd.DataFrame(index=fd.index)
    iso = fd.index.isocalendar()
    week_key = iso["year"].astype(str) + "-W" + iso["week"].astype(str)
    month_key = fd.index.to_period("M")

    for col, pfx in prefix_map:
        s = fd[col].astype(float)
        out[f"{pfx}_Last_Day"] = s.shift(1)
        out[f"{pfx}_Curr_Day"] = s
        out[f"{pfx}_Next_Day"] = s.shift(-1)
        out[f"{pfx}_Last_Week"] = s.shift(1).rolling(7, min_periods=7).sum()
        out[f"{pfx}_Curr_Week"] = s.groupby(week_key.values).transform("sum")
        out[f"{pfx}_Next_Week"] = s.rolling(7, min_periods=7).sum().shift(-7)
        out[f"{pfx}_Last_Month"] = s.shift(1).rolling(30, min_periods=30).sum()
        out[f"{pfx}_Curr_Month"] = s.groupby(month_key).transform("sum")
        out[f"{pfx}_Next_Month"] = s.rolling(30, min_periods=30).sum().shift(-30)
    return out.reset_index()


def add_flight_features(df: pd.DataFrame, park: str,
                        cache_dir: Path) -> tuple[pd.DataFrame, list[str]]:
    airports = PARK_AIRPORTS[park]
    start = df["Date"].min() - timedelta(days=35)
    end = df["Date"].max() + timedelta(days=35)
    flight_daily = daily_airport_counts(range(start.year, end.year + 1),
                                        airports, cache_dir)
    flight_daily = flight_daily[(flight_daily["Date"] >= start)
                                & (flight_daily["Date"] <= end)]
    feats = build_flight_window_features(flight_daily)
    cols = [c for c in feats.columns if c != "Date"]
    df = df.drop(columns=[c for c in cols if c in df.columns])
    return df.merge(feats, on="Date", how="left"), cols


# --------------------------------------------------------------------------
# Lags + extended feature pack
# --------------------------------------------------------------------------
def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("Date").reset_index(drop=True)
    df["Entries_Lag_1"] = df["Entries"].shift(1)
    df["Entries_Lag_7"] = df["Entries"].shift(7)
    df["Entries_Roll_Mean_7"] = df["Entries"].shift(1).rolling(7).mean()
    return df


def _daily_time_span(fp, date_cands, start_cands, end_cands):
    head = pd.read_csv(fp, nrows=0, sep=None, engine="python")
    lowc = {c.lower(): c for c in head.columns}
    dcol = next((lowc[c] for c in date_cands if c in lowc), None)
    scol = next((lowc[c] for c in start_cands if c in lowc), None)
    ecol = next((lowc[c] for c in end_cands if c in lowc), None)
    if not (dcol and scol and ecol):
        raise ValueError(f"columns not recognized in {fp.name}")
    t = pd.read_csv(fp, usecols=list(dict.fromkeys([dcol, scol, ecol])),
                    sep=None, engine="python")
    t[dcol] = pd.to_datetime(t[dcol], errors="coerce")
    for c in (scol, ecol):
        t[c] = pd.to_datetime(t[c], errors="coerce")
    t = t.dropna()
    g = t.groupby(t[dcol].dt.normalize()).agg(_open=(scol, "min"),
                                              _close=(ecol, "max"))
    hrs = ((g["_close"] - g["_open"]).dt.total_seconds() / 3600).clip(2, 24)
    return hrs.rename("Open_Hours").reset_index().rename(columns={dcol: "Date"})


def add_pack2_features(df: pd.DataFrame, files, park: str) -> pd.DataFrame:
    """Easter/bridge/holiday-distance, longer lags, flight ratios,
    operating hours and lagged wait stats (best effort)."""
    from dateutil.easter import easter

    df = df.sort_values("Date").reset_index(drop=True).copy()
    years = range(df["Date"].dt.year.min() - 1, df["Date"].dt.year.max() + 2)
    hol = _holiday_calendar(park, years)

    if hol is not None:
        all_days = pd.date_range(df["Date"].min() - pd.Timedelta(days=40),
                                 df["Date"].max() + pd.Timedelta(days=40),
                                 freq="D")
        hol_dates = pd.DatetimeIndex([d for d in all_days if d in hol])
        if len(hol_dates):
            idx = hol_dates.values
            pos = np.searchsorted(idx, df["Date"].values)
            nxt = idx[np.clip(pos, 0, len(idx) - 1)]
            prv = idx[np.clip(pos - 1, 0, len(idx) - 1)]
            df["Days_To_Holiday"] = np.clip(
                (nxt - df["Date"].values).astype("timedelta64[D]").astype(int), 0, 30)
            df["Days_Since_Holiday"] = np.clip(
                (df["Date"].values - prv).astype("timedelta64[D]").astype(int), 0, 30)
        nxt_hol = df["Date"].apply(lambda d: (d + pd.Timedelta(days=1)) in hol)
        prv_hol = df["Date"].apply(lambda d: (d - pd.Timedelta(days=1)) in hol)
        df["Is_Bridge_Day"] = (((df["DayOfWeek"] == 0) & nxt_hol)
                               | ((df["DayOfWeek"] == 4) & prv_hol)).astype(int) \
            * (1 - df["Is_Holiday"])

    easters = {y: pd.Timestamp(easter(y)) for y in years}
    df["Is_Easter_Week"] = df["Date"].apply(
        lambda d: int(easters[d.year] - pd.Timedelta(days=7) <= d
                      <= easters[d.year] + pd.Timedelta(days=1)))
    df["Is_Xmas_Period"] = (((df["Date"].dt.month == 12) & (df["Date"].dt.day >= 20))
                            | ((df["Date"].dt.month == 1)
                               & (df["Date"].dt.day <= 6))).astype(int)
    df["Is_Summer_Peak"] = df["Date"].dt.month.isin([7, 8]).astype(int)

    df["Entries_Lag_14"] = df["Entries"].shift(14)
    df["Entries_Lag_364"] = df["Entries"].shift(364)
    df["Entries_SameDOW_Mean4"] = (df.groupby("DayOfWeek")["Entries"]
                                   .transform(lambda s: s.shift(1)
                                              .rolling(4, min_periods=2).mean()))
    df["Entries_Roll_Std_7"] = df["Entries"].shift(1).rolling(7).std()
    df["Entries_WoW_Diff"] = df["Entries_Lag_1"] - df["Entries_Lag_7"]

    eps = 1e-6
    if "Arr_Next_Week" in df.columns:
        df["Arr_Week_Momentum"] = df["Arr_Next_Week"] / (df["Arr_Last_Week"] + eps)
        df["Dep_Week_Momentum"] = df["Dep_Next_Week"] / (df["Dep_Last_Week"] + eps)
        df["Arr_Curr_vs_Trail"] = df["Arr_Curr_Week"] / (df["Arr_Last_Month"] * 7 / 30 + eps)
        df["Dep_Curr_vs_Trail"] = df["Dep_Curr_Week"] / (df["Dep_Last_Month"] * 7 / 30 + eps)
        for c in ("Arr_Week_Momentum", "Dep_Week_Momentum",
                  "Arr_Curr_vs_Trail", "Dep_Curr_vs_Trail"):
            df[c] = df[c].replace([np.inf, -np.inf], np.nan).clip(0, 10)

    sched = _find_file(files, "schedule")
    wait = _find_file(files, "waiting")
    hours = None
    for fp in (sched, wait):
        if fp is None or hours is not None:
            continue
        try:
            hours = _daily_time_span(fp,
                                     ("work_date", "usage_date", "date", "deb_time"),
                                     ("deb_time", "open_time", "opening_time"),
                                     ("fin_time", "close_time", "closing_time"))
        except Exception as e:  # noqa: BLE001
            print(f"note: Open_Hours via {fp.name} skipped ({e})")
    if hours is not None:
        df = df.merge(hours, on="Date", how="left")

    try:
        if wait is None:
            raise ValueError("no waiting_times file")
        head = pd.read_csv(wait, nrows=0, sep=None, engine="python")
        lowc = {c.lower(): c for c in head.columns}
        dcol = next((lowc[c] for c in ("work_date", "usage_date", "date")
                     if c in lowc), None)
        wcol = next((lowc[c] for c in ("wait_time_max", "wait_time", "posted_wait")
                     if c in lowc), None)
        if not (dcol and wcol):
            raise ValueError("wait columns not recognized")
        wt = pd.read_csv(wait, usecols=[dcol, wcol], sep=None, engine="python")
        wt[dcol] = pd.to_datetime(wt[dcol], errors="coerce")
        wt[wcol] = pd.to_numeric(wt[wcol], errors="coerce")
        daily_wait = (wt.dropna().groupby(wt[dcol].dt.normalize())[wcol]
                      .mean().rename("Wait_Mean"))
        cont = daily_wait.reindex(pd.date_range(daily_wait.index.min(),
                                                daily_wait.index.max()))
        lagged = pd.DataFrame({"Date": cont.index,
                               "Wait_Mean_Lag_1": cont.shift(1).values,
                               "Wait_Mean_Lag_7": cont.shift(7).values})
        df = df.merge(lagged, on="Date", how="left")
    except Exception as e:  # noqa: BLE001
        print(f"note: lagged wait stats skipped ({e})")
    return df


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def build_feature_frame(files, park: str, cache_dir: Path) -> pd.DataFrame:
    """Full frame: Date, Entries, and every model feature (NaNs kept where
    XGBoost can use them; core rows with missing short lags are dropped)."""
    df = load_attendance(files, park)
    df, _ = add_weather_features(df, files)
    df = add_calendar_features(df, park)
    df, _ = add_flight_features(df, park, cache_dir)
    df = add_lag_features(df)
    core = ["Entries_Lag_1", "Entries_Lag_7", "Entries_Roll_Mean_7",
            "Arr_Curr_Day", "Dep_Curr_Day"]
    df = df.dropna(subset=[c for c in core if c in df.columns]).reset_index(drop=True)
    df = add_pack2_features(df, files, park)
    df["Date_Num"] = df["Date"].dt.strftime("%Y%m%d").astype(int)
    return df


# --------------------------------------------------------------------------
# Synthetic generators (offline demo + tests)
# --------------------------------------------------------------------------
def synth_kaggle_files(dst_dir: Path, seed: int = 7) -> list[Path]:
    """Write synthetic Kaggle-shaped files (attendance, weather, schedule,
    waiting times) and return their paths."""
    rng = np.random.default_rng(seed)
    d = Path(dst_dir)
    d.mkdir(parents=True, exist_ok=True)
    days = pd.date_range("2018-06-01", "2022-07-31", freq="D")
    rows = []
    for park, base in [("PortAventura World", 9000), ("Tivoli Gardens", 11000)]:
        for dt in days:
            if park == "PortAventura World" and dt.month in (1, 2):
                continue
            if pd.Timestamp("2020-03-14") <= dt <= pd.Timestamp("2021-05-15"):
                if rng.random() < 0.8:
                    continue
                val = int(rng.choice([-1, 0]))
            else:
                wknd = 1.5 if dt.dayofweek in (5, 6) else 1.0
                val = max(int(base * wknd
                              * (0.8 + 0.4 * np.sin(2 * np.pi * dt.dayofyear / 365))
                              + rng.integers(500, 4000)), 300)
            rows.append({"USAGE_DATE": dt.strftime("%Y-%m-%d"),
                         "FACILITY_NAME": park, "attendance": val})
    att = pd.DataFrame(rows)
    att.to_csv(d / "attendance.csv", index=False)

    hours = pd.date_range("2018-04-01", "2022-09-30 23:00", freq="h")
    doy = np.asarray(hours.dayofyear)
    pd.DataFrame({
        "dt_iso": hours.strftime("%Y-%m-%d %H:%M:%S") + " +0000 UTC",
        "temp": 18 + 10 * np.sin(2 * np.pi * doy / 365) + rng.normal(0, 2, len(hours)),
        "temp_min": 14 + rng.normal(0, 2, len(hours)),
        "temp_max": 24 + rng.normal(0, 2, len(hours)),
        "humidity": rng.integers(40, 95, len(hours)),
        "wind_speed": rng.uniform(0, 12, len(hours)).round(1),
        "rain_1h": np.where(rng.random(len(hours)) < 0.9, np.nan,
                            rng.uniform(0.1, 6, len(hours))),
        "clouds_all": rng.integers(0, 100, len(hours)),
    }).to_csv(d / "weather_data.csv", index=False)

    op_days = pd.to_datetime(att.loc[att["attendance"] > 0, "USAGE_DATE"]).unique()
    wt_rows, es_rows = [], []
    for dt in op_days:
        dt = pd.Timestamp(dt)
        close_h = 23 if dt.month in (6, 7, 8) else 19
        es_rows.append({"ENTITY_DESCRIPTION_SHORT": "PW", "ENTITY_TYPE": "PARK",
                        "DEB_TIME": f"{dt:%Y-%m-%d} 10:00:00",
                        "FIN_TIME": f"{dt:%Y-%m-%d} {close_h}:00:00"})
        for h in range(10, close_h, 3):
            wt_rows.append({"WORK_DATE": dt.strftime("%Y-%m-%d"),
                            "DEB_TIME": f"{dt:%Y-%m-%d} {h:02d}:00:00",
                            "FIN_TIME": f"{dt:%Y-%m-%d} {h:02d}:15:00",
                            "ENTITY_DESCRIPTION_SHORT": "Dragon Khan",
                            "WAIT_TIME_MAX": int(rng.integers(5, 90))})
    pd.DataFrame(wt_rows).to_csv(d / "waiting_times.csv", index=False)
    pd.DataFrame(es_rows).to_csv(d / "entity_schedule.csv", index=False)
    return sorted(p for p in d.rglob("*") if p.is_file())


def synth_eurocontrol_cache(cache_dir: Path, years, seed: int = 7) -> None:
    """Pre-seed the flight cache so daily_airport_counts() runs offline."""
    rng = np.random.default_rng(seed)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    days = pd.date_range(f"{min(years)}-01-01", f"{max(years)}-12-31", freq="D")
    doy = np.asarray(days.dayofyear, dtype=float)
    covid = np.asarray((days >= "2020-03-15") & (days <= "2021-06-01"))
    rows = []
    for icao, base in [("LEBL", 480), ("LERS", 25), ("EKCH", 380)]:
        lvl = np.where(covid, base * 0.15, base) \
            * (1 + 0.15 * np.sin(2 * np.pi * doy / 365))
        dep = np.clip(lvl / 2 + rng.integers(-10, 10, len(days)), 1, None).astype(int)
        arr = np.clip(lvl / 2 + rng.integers(-10, 10, len(days)), 1, None).astype(int)
        for i, dt in enumerate(days):
            rows.append({"YEAR": dt.year, "MONTH_NUM": dt.month,
                         "FLT_DATE": dt.strftime("%d-%b-%Y").upper(),
                         "APT_ICAO": icao, "APT_NAME": icao, "STATE_NAME": "X",
                         "FLT_DEP_1": dep[i], "FLT_ARR_1": arr[i],
                         "FLT_TOT_1": dep[i] + arr[i]})
    full = pd.DataFrame(rows)
    for y, grp in full.groupby("YEAR"):
        grp.to_csv(cache_dir / f"airport_traffic_{y}.csv", index=False)
