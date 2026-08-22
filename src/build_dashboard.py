"""Build the static forecast dashboard.

Story: predict daily attendance far enough ahead to set labor schedules and
time ad spend. Two models are trained on all but the final 30 operating days:

- week-ahead  (headline): only features knowable >= 7 days before the target
  day — the schedule-lock / media-buy horizon.
- day-ahead   (reference): all features, including yesterday's attendance —
  the ceiling for day-of fine-tuning.

The final 30 days are treated as a live forecast and every panel measures
decision quality: staffing cost of error, soft-day detection for marketing,
systematic biases, and drivers.

Usage:
    python src/build_dashboard.py                 # real data (Kaggle + EUROCONTROL)
    python src/build_dashboard.py --synthetic     # offline demo build
    python src/build_dashboard.py --park "Tivoli Gardens"
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor

import pipeline as pl

XGB_PARAMS = dict(n_estimators=1200, learning_rate=0.02, max_depth=6,
                  subsample=0.8, colsample_bytree=0.8, min_child_weight=4,
                  random_state=pl.RANDOM_STATE, n_jobs=-1)

# Features that require data newer than 7 days before the target day.
# Excluded from the week-ahead model so its accuracy is honest at the
# horizon where staffing and ad decisions are actually made.
WEEK_AHEAD_DROP = ["Entries_Lag_1", "Entries_Roll_Mean_7", "Entries_Roll_Std_7",
                   "Entries_WoW_Diff", "Wait_Mean_Lag_1"]

ROOT = Path(__file__).resolve().parents[1]


def _fit(X, y_raw):
    return XGBRegressor(**XGB_PARAMS).fit(np.asarray(X, float), np.log1p(y_raw))


def _predict(model, X):
    return np.clip(np.expm1(model.predict(np.asarray(X, float))), 0, None)


def permutation_deltas(model, X_val, y_val, columns, n_repeats=5,
                       seed=pl.RANDOM_STATE):
    """MAE increase when each feature's validation values are shuffled."""
    rng = np.random.default_rng(seed)
    X_val = np.asarray(X_val, float)
    base = mean_absolute_error(y_val, _predict(model, X_val))
    out = {}
    for j, col in enumerate(columns):
        deltas = []
        for _ in range(n_repeats):
            Xs = X_val.copy()
            Xs[:, j] = rng.permutation(Xs[:, j])
            deltas.append(mean_absolute_error(y_val, _predict(model, Xs)) - base)
        out[col] = float(np.mean(deltas))
    return out


def _metrics(actual, pred):
    err = actual - pred  # positive = under-forecast
    return {"mae": float(np.abs(err).mean()),
            "wape": float(np.abs(err).sum() / np.abs(actual).sum()),
            "bias": float(err.mean())}


def _clean(obj):
    """JSON-safe: numpy scalars -> python, NaN/inf -> None, floats rounded."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, np.ndarray, pd.Series)):
        return [_clean(v) for v in list(obj)]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 3)
    return obj


def build(synthetic: bool, park: str, out_path: Path, holdout_days: int = 30,
          context_days: int = 45) -> Path:
    cache_dir = ROOT / "flight_cache"
    if synthetic:
        tmp = Path(tempfile.mkdtemp(prefix="synth_kaggle_"))
        files = pl.synth_kaggle_files(tmp)
        pl.synth_eurocontrol_cache(cache_dir, range(2018, 2023))
        mode = "synthetic demo"
    else:
        files = pl.download_kaggle_files()
        mode = "real data"

    df = pl.build_feature_frame(files, park, cache_dir)
    if len(df) <= holdout_days + 60:
        raise RuntimeError(f"not enough rows ({len(df)}) for a "
                           f"{holdout_days}-day holdout")

    all_cols = [c for c in df.columns if c not in ("Date", "Entries")]
    week_cols = [c for c in all_cols if c not in WEEK_AHEAD_DROP]
    y = df["Entries"].values.astype(float)

    hold = np.arange(len(df) - holdout_days, len(df))
    train = np.arange(len(df) - holdout_days)
    actual = y[hold]

    # ---- two horizons ----
    model_day = _fit(df[all_cols].iloc[train], y[train])
    model_week = _fit(df[week_cols].iloc[train], y[train])
    pred_day = _predict(model_day, df[all_cols].iloc[hold])
    pred_week = _predict(model_week, df[week_cols].iloc[hold])
    err_day = actual - pred_day
    err_week = actual - pred_week

    m_day = _metrics(actual, pred_day)
    m_week = _metrics(actual, pred_week)
    worst_i = int(np.argmax(np.abs(err_week)))

    # ---- soft-day detection (marketing lens, week-ahead) ----
    k = max(1, round(holdout_days * 0.25))
    actual_soft = sorted(int(i) for i in np.argsort(actual)[:k])
    pred_soft = sorted(int(i) for i in np.argsort(pred_week)[:k])
    hits = len(set(actual_soft) & set(pred_soft))

    # ---- rule-of-thumb baselines (roll forward with realized actuals) ----
    emap = dict(zip(df["Date"], y))
    hold_ts = list(df["Date"].iloc[hold])

    def _naive(offsets, min_n=1):
        vals = []
        for D in hold_ts:
            xs = [emap.get(D - pd.Timedelta(days=o)) for o in offsets]
            xs = [x for x in xs if x is not None]
            vals.append(float(np.mean(xs)) if len(xs) >= min_n else None)
        return vals

    baseline_defs = [
        ("Same weekday last week", _naive([7])),
        ("Same weekday last year", _naive([364])),
        ("Avg of last 4 same weekdays", _naive([7, 14, 21, 28], min_n=2)),
    ]
    baselines = []
    for name, vals in baseline_defs:
        mask = np.array([v is not None for v in vals])
        if not mask.any():
            continue
        bv = np.array([v for v in vals if v is not None], float)
        a_sub = actual[mask]
        baselines.append({
            "name": name, "n": int(mask.sum()),
            "mae": float(np.abs(a_sub - bv).mean()),
            "model_mae_same_days": float(np.abs(a_sub - pred_week[mask]).mean()),
        })
    eligible = [b for b in baselines if b["n"] >= min(20, holdout_days)]
    best = min(eligible or baselines, key=lambda b: b["mae"])
    best_baseline = dict(best)
    best_baseline["skill"] = (float((best["mae"] - best["model_mae_same_days"])
                                    / best["mae"]) if best["mae"] > 0 else None)

    # ---- tolerance hit rates (model vs strongest baseline) ----
    tol_thresholds = [5, 10, 15, 20]
    pct_err_model = np.abs(err_week) / np.maximum(actual, 1e-9) * 100
    tol_model = [float((pct_err_model <= t).mean()) for t in tol_thresholds]
    bb_vals = dict(baseline_defs)[best["name"]]
    bmask = np.array([v is not None for v in bb_vals])
    bvv = np.array([v for v in bb_vals if v is not None], float)
    pct_err_base = (np.abs(actual[bmask] - bvv)
                    / np.maximum(actual[bmask], 1e-9) * 100)
    tol_base = [float((pct_err_base <= t).mean()) for t in tol_thresholds]

    # ---- drivers of the deployable (week-ahead) forecast ----
    tr_i, va_i = train_test_split(train, test_size=0.2,
                                  random_state=pl.RANDOM_STATE)
    perm_model = _fit(df[week_cols].iloc[tr_i], y[tr_i])
    deltas = permutation_deltas(perm_model, df[week_cols].iloc[va_i],
                                y[va_i], week_cols)
    importance = sorted(({"feature": f, "delta": d} for f, d in deltas.items()),
                        key=lambda d: d["delta"], reverse=True)[:15]

    # ---- empirical 80% band from validation residual ratios ----
    ratio_va = (y[va_i]
                / np.maximum(_predict(perm_model, df[week_cols].iloc[va_i]), 1e-9))
    q_lo, q_hi = np.quantile(ratio_va, [0.10, 0.90])
    band_lo = pred_week * q_lo
    band_hi = pred_week * q_hi
    band_cov = int(((actual >= band_lo) & (actual <= band_hi)).sum())

    hold_dates = df["Date"].iloc[hold].dt.strftime("%Y-%m-%d").tolist()
    ctx = df.iloc[max(0, train[-1] + 1 - context_days): train[-1] + 1]

    dow = df["Date"].iloc[hold].dt.dayofweek.values
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekday = {"labels": [], "mean_error": []}
    for d in range(7):
        msk = dow == d
        if msk.any():
            weekday["labels"].append(labels[d])
            weekday["mean_error"].append(float(err_week[msk].mean()))

    features_payload = {
        c: {"group": pl.feature_group(c),
            "in_week_model": c in week_cols,
            "values": df[c].iloc[hold].tolist()}
        for c in all_cols if c != "Date_Num"
    }

    data = _clean({
        "meta": {
            "park": park, "mode": mode,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "window_start": hold_dates[0], "window_end": hold_dates[-1],
            "n_train": int(len(train)),
            "week": m_week, "day": m_day,
            "soft": {"k": k, "hits": hits, "false_alarms": k - hits,
                     "actual_idx": actual_soft, "pred_idx": pred_soft},
            "worst": {"date": hold_dates[worst_i],
                      "err": float(err_week[worst_i])},
            "avg_attendance": float(actual.mean()),
            "week_ahead_dropped": [c for c in WEEK_AHEAD_DROP if c in all_cols],
            "baselines": baselines,
            "best_baseline": best_baseline,
            "tolerance": {"thresholds": tol_thresholds, "model": tol_model,
                          "baseline": tol_base, "baseline_name": best["name"],
                          "baseline_n": best["n"]},
            "band": {"coverage": band_cov, "q_lo": float(q_lo),
                     "q_hi": float(q_hi)},
        },
        "context": {"dates": ctx["Date"].dt.strftime("%Y-%m-%d").tolist(),
                    "actual": ctx["Entries"].tolist()},
        "holdout": {"dates": hold_dates, "actual": actual.tolist(),
                    "pred_week": pred_week.tolist(), "err_week": err_week.tolist(),
                    "pred_day": pred_day.tolist(), "err_day": err_day.tolist(),
                    "band_lo": band_lo.tolist(), "band_hi": band_hi.tolist()},
        "features": features_payload,
        "importance": importance,
        "weekday": weekday,
    })

    template = (ROOT / "src" / "dashboard_template.html").read_text()
    token = "/*__DASHBOARD_DATA__*/null"
    if token not in template:
        raise RuntimeError("template placeholder missing")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(template.replace(token, json.dumps(data)))

    print(f"[{mode}] {park}: trained on {len(train)} days, "
          f"forecast {hold_dates[0]} -> {hold_dates[-1]}")
    print(f"  week-ahead MAE {m_week['mae']:.1f} (WAPE {m_week['wape']:.1%}) | "
          f"day-ahead MAE {m_day['mae']:.1f} (WAPE {m_day['wape']:.1%})")
    print(f"  soft days caught {hits}/{k} | skill vs '{best['name']}': "
          f"{best_baseline['skill']:+.1%} | band coverage {band_cov}/{holdout_days}")
    print(f"  dashboard -> {out_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--synthetic", action="store_true",
                    help="offline demo build with synthetic data")
    ap.add_argument("--park", default="PortAventura World",
                    choices=list(pl.PARK_AIRPORTS))
    ap.add_argument("--out", default=str(ROOT / "public" / "index.html"))
    ap.add_argument("--holdout-days", type=int, default=30)
    args = ap.parse_args()
    build(args.synthetic, args.park, Path(args.out), args.holdout_days)


if __name__ == "__main__":
    main()
