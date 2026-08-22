"""Model-selection backtest for the attendance forecast (evidence for the
shipped blend - run with: python src/backtest.py).

Rolling-origin cross-validation entirely inside the training period: the
final 30-day dashboard holdout is NEVER touched here. Each fold trains on
everything before it and predicts the next 30 operating days, mimicking
exactly how the dashboard's week-ahead model is used.

Candidates span algorithms (XGBoost, LightGBM, HistGB, Ridge, RF, MLP),
week-safe feature additions, objectives, and post-hoc bias corrections.

Note: the selection run that picked the shipped blend was executed against
the pre-blend pipeline (before the week-safe features were merged into
pipeline.py), so its "current feats" baseline reflects that older feature
set; pipeline.py now ships the winning features, making add_week_safe_features
here a no-op recomputation kept for self-containment.
"""
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as pl

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = 30
N_FOLDS = 4
FOLD = 30

WEEK_AHEAD_DROP = ["Entries_Lag_1", "Entries_Roll_Mean_7", "Entries_Roll_Std_7",
                   "Entries_WoW_Diff", "Wait_Mean_Lag_1"]

XGB_BASE = dict(n_estimators=1200, learning_rate=0.02, max_depth=6,
                subsample=0.8, colsample_bytree=0.8, min_child_weight=4,
                random_state=pl.RANDOM_STATE, n_jobs=-1)


def add_week_safe_features(df):
    df = df.copy()
    e = df["Entries"]
    df["Entries_Lag_21"] = e.shift(21)
    df["Entries_Lag_28"] = e.shift(28)
    df["Entries_WkSafe_Mean7"] = e.shift(7).rolling(7).mean()
    df["Entries_WkSafe_Std7"] = e.shift(7).rolling(7).std()
    df["Entries_Lag7_minus_14"] = df["Entries_Lag_7"] - df["Entries_Lag_14"]
    doy = df["Date"].dt.dayofyear
    df["DayOfYear_Sin2"] = np.sin(4 * np.pi * doy / 365.25)
    df["DayOfYear_Cos2"] = np.cos(4 * np.pi * doy / 365.25)
    return df


# ---------------- model zoo ----------------
def make_model(kind, params=None):
    if kind == "xgb":
        from xgboost import XGBRegressor
        return XGBRegressor(**(params or XGB_BASE))
    if kind == "lgbm":
        from lightgbm import LGBMRegressor
        return LGBMRegressor(**(params or dict(
            n_estimators=1500, learning_rate=0.02, num_leaves=31,
            subsample=0.8, colsample_bytree=0.8, min_child_samples=10,
            objective="l1", random_state=pl.RANDOM_STATE, n_jobs=-1,
            verbose=-1)))
    if kind == "histgb":
        from sklearn.ensemble import HistGradientBoostingRegressor
        return HistGradientBoostingRegressor(**(params or dict(
            loss="absolute_error", max_iter=800, learning_rate=0.04,
            max_depth=None, max_leaf_nodes=31, min_samples_leaf=10,
            l2_regularization=1.0, random_state=pl.RANDOM_STATE)))
    if kind == "rf":
        from sklearn.ensemble import RandomForestRegressor
        return RandomForestRegressor(n_estimators=600, min_samples_leaf=3,
                                     max_features=0.5, n_jobs=-1,
                                     random_state=pl.RANDOM_STATE)
    raise ValueError(kind)


def make_linearish(kind, cat_cols, num_cols):
    """Ridge / MLP with imputation, one-hot DOW/Month, scaling."""
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    num = Pipeline([("imp", SimpleImputer(strategy="median")),
                    ("sc", StandardScaler())])
    cat = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    ct = ColumnTransformer([("num", num, num_cols), ("cat", cat, cat_cols)])
    if kind == "ridge":
        from sklearn.linear_model import RidgeCV
        est = RidgeCV(alphas=np.logspace(-2, 3, 20))
    elif kind == "mlp":
        from sklearn.neural_network import MLPRegressor
        est = MLPRegressor(hidden_layer_sizes=(64, 32), alpha=1e-2,
                           learning_rate_init=3e-3, max_iter=600,
                           early_stopping=True, n_iter_no_change=25,
                           random_state=pl.RANDOM_STATE)
    else:
        raise ValueError(kind)
    return Pipeline([("prep", ct), ("est", est)])


CAT_COLS = ["DayOfWeek", "Month"]


def fit_predict(kind, df, cols, tr, te, params=None, log_target=True):
    y = df["Entries"].values.astype(float)
    if kind in ("ridge", "mlp"):
        cats = [c for c in CAT_COLS if c in cols]
        nums = [c for c in cols if c not in cats]
        m = make_linearish(kind, cats, nums)
        Xtr, Xte = df[cols].iloc[tr], df[cols].iloc[te]
        if log_target:
            m.fit(Xtr, np.log1p(y[tr]))
            return np.clip(np.expm1(m.predict(Xte)), 0, None)
        m.fit(Xtr, y[tr])
        return np.clip(m.predict(Xte), 0, None)
    m = make_model(kind, params)
    Xtr = np.asarray(df[cols].iloc[tr], float)
    Xte = np.asarray(df[cols].iloc[te], float)
    if log_target:
        m.fit(Xtr, np.log1p(y[tr]))
        return np.clip(np.expm1(m.predict(Xte)), 0, None)
    m.fit(Xtr, y[tr])
    return np.clip(m.predict(Xte), 0, None)


def weekday_correction(kind, df, cols, tr, params=None, log_target=True,
                       nested=60, shrink_k=4.0):
    if len(tr) <= nested + 90:
        return {}
    tr_fit, tr_val = tr[:-nested], tr[-nested:]
    pv = fit_predict(kind, df, cols, tr_fit, tr_val, params, log_target)
    resid = df["Entries"].values[tr_val] - pv
    dows = df["Date"].iloc[tr_val].dt.dayofweek.values
    corr = {}
    for d in range(7):
        msk = dows == d
        n = int(msk.sum())
        if n:
            corr[d] = float(resid[msk].mean()) * n / (n + shrink_k)
    return corr


def run(df, name, kind, cols, params=None, log_target=True, wd_corr=False,
        mean_corr=False, blend=None):
    """blend: (other_kind, weight_of_other) or ('samedow', w)."""
    y = df["Entries"].values.astype(float)
    n = len(df)
    maes, biases = [], []
    for f in range(N_FOLDS):
        te_end = n - HOLDOUT - f * FOLD
        te = np.arange(te_end - FOLD, te_end)
        tr = np.arange(te_end - FOLD)
        pred = fit_predict(kind, df, cols, tr, te, params, log_target)
        if blend:
            other, w = blend
            if other == "samedow":
                sd = df["Entries_SameDOW_Mean4"].iloc[te].values
                ok = ~np.isnan(sd)
                pred[ok] = (1 - w) * pred[ok] + w * sd[ok]
            else:
                p2 = fit_predict(other, df, cols, tr, te, None, log_target)
                pred = (1 - w) * pred + w * p2
        if wd_corr or mean_corr:
            corr = weekday_correction(kind, df, cols, tr, params, log_target)
            if wd_corr and corr:
                dows = df["Date"].iloc[te].dt.dayofweek.values
                pred = pred + np.array([corr.get(d, 0.0) for d in dows])
            elif mean_corr and corr:
                pred = pred + np.mean(list(corr.values()))
        e = y[te] - pred
        maes.append(np.abs(e).mean())
        biases.append(e.mean())
    r = {"name": name, "mae": float(np.mean(maes)),
         "folds": [round(float(m), 1) for m in maes],
         "bias": float(np.mean(biases))}
    print(f"{r['name']:38s} MAE {r['mae']:7.1f}  folds {r['folds']}"
          f"  bias {r['bias']:+7.1f}", flush=True)
    return r


def main():
    tmp = Path(tempfile.mkdtemp(prefix="synth_exp_"))
    files = pl.synth_kaggle_files(tmp)
    pl.synth_eurocontrol_cache(ROOT / "flight_cache", range(2018, 2023))
    df0 = pl.build_feature_frame(files, "PortAventura World",
                                 ROOT / "flight_cache")
    df = add_week_safe_features(df0)

    all_cols = [c for c in df0.columns if c not in ("Date", "Entries")]
    week0 = [c for c in all_cols if c not in WEEK_AHEAD_DROP]
    new_feats = ["Entries_Lag_21", "Entries_Lag_28", "Entries_WkSafe_Mean7",
                 "Entries_WkSafe_Std7", "Entries_Lag7_minus_14",
                 "DayOfYear_Sin2", "DayOfYear_Cos2"]
    week1 = week0 + new_feats

    print(f"rows={len(df)}  folds={N_FOLDS}x{FOLD}d  (30d holdout untouched)")
    print(f"irreducible-noise note: synth noise ~U(500,4000) -> MAE floor ~875\n")

    R = []
    # --- algorithm sweep on current features ---
    R.append(run(df, "xgb / current feats (baseline)", "xgb", week0))
    R.append(run(df, "xgb / +week-safe feats", "xgb", week1))
    xgb_d4 = dict(XGB_BASE, max_depth=4)
    xgb_d4_abs = dict(XGB_BASE, max_depth=4, objective="reg:absoluteerror")
    xgb_abs = dict(XGB_BASE, objective="reg:absoluteerror")
    R.append(run(df, "xgb d4 / +feats", "xgb", week1, params=xgb_d4))
    R.append(run(df, "xgb abs-loss / +feats", "xgb", week1, params=xgb_abs))
    R.append(run(df, "xgb d4+abs / +feats", "xgb", week1, params=xgb_d4_abs))
    R.append(run(df, "lgbm l1 / +feats", "lgbm", week1))
    R.append(run(df, "histgb abs / +feats", "histgb", week1))
    R.append(run(df, "rf / +feats", "rf", week1))
    R.append(run(df, "ridge onehot / +feats", "ridge", week1))
    R.append(run(df, "mlp 64x32 / +feats", "mlp", week1))
    # --- corrections & blends on the promising ones ---
    R.append(run(df, "xgb d4 +feats +wdcorr", "xgb", week1, params=xgb_d4,
                 wd_corr=True))
    R.append(run(df, "xgb d4 +feats +meancorr", "xgb", week1, params=xgb_d4,
                 mean_corr=True))
    R.append(run(df, "xgb d4 +feats blend ridge .3", "xgb", week1,
                 params=xgb_d4, blend=("ridge", 0.3)))
    R.append(run(df, "xgb d4 +feats blend samedow .2", "xgb", week1,
                 params=xgb_d4, blend=("samedow", 0.2)))
    R.append(run(df, "lgbm blend ridge .3", "lgbm", week1,
                 blend=("ridge", 0.3)))
    R.append(run(df, "histgb blend ridge .3", "histgb", week1,
                 blend=("ridge", 0.3)))

    best = min(R, key=lambda r: r["mae"])
    print(f"\nBEST: {best['name']}  MAE {best['mae']:.1f} "
          f"(baseline {R[0]['mae']:.1f}, "
          f"delta {best['mae'] - R[0]['mae']:+.1f})")


if __name__ == "__main__":
    main()
