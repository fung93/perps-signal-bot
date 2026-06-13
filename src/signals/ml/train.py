"""Train the LightGBM classifier behind a walk-forward promotion gate (Phase 4).

Builds the dataset from candle history (a backfill CSV if present, else fetched), trains on
the older portion, evaluates AUC on the held-out recent portion, and promotes the model to
'active' in the params table ONLY if it beats the incumbent out-of-sample (and a minimum
AUC). The model is serialised into params.payload (JSONB) so predict can load it with no
file. Rejected candidates are still recorded (is_active = false) for the audit trail.

    python -m src.signals.ml.train
"""
from __future__ import annotations

import glob
import logging
import os
from datetime import datetime, timedelta, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb
from scipy.stats import rankdata

from ... import config, db
from ...data import binance
from .features import FEATURE_COLUMNS, build_dataset

logger = logging.getLogger(__name__)

TRAIN_DAYS = 365
TEST_FRACTION = 0.30
MIN_AUC = 0.52          # a candidate must clear this (better than random) to be considered
DATA_DIR = "data"

_LGB_PARAMS = {
    "objective": "binary",
    "num_leaves": 15,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "min_data_in_leaf": 50,
    "verbose": -1,
}
_NUM_ROUNDS = 200


def _auc(y, p) -> float:
    """ROC AUC via the Mann-Whitney statistic (no scikit-learn dependency)."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    n_pos = y.sum()
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    r = rankdata(p)
    return float((r[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _load_candles() -> dict[str, pd.DataFrame]:
    """Per-coin candle history on the signal timeframe: newest backfill CSV, else fetch."""
    csvs = sorted(glob.glob(os.path.join(DATA_DIR, "candles_backfill_*.csv")))
    if csvs:
        logger.info("loading candles from %s", csvs[-1])
        df = pd.read_csv(csvs[-1])
        df = df[df["timeframe"] == config.SIGNAL_TIMEFRAME].copy()
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        return {coin: g for coin, g in df.groupby("coin")}

    logger.info("no backfill CSV; fetching %d days from Binance", TRAIN_DAYS)
    start = datetime.now(timezone.utc) - timedelta(days=TRAIN_DAYS)
    out: dict[str, pd.DataFrame] = {}
    for coin in config.COINS:
        candles = binance.fetch_range(coin, config.SIGNAL_TIMEFRAME, start)
        out[coin] = pd.DataFrame([
            {"open_time": c.open_time, "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume} for c in candles])
    return out


def _assemble() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Pool coins into a time-sorted frame and split chronologically into train/test."""
    frames = []
    for coin, candles in _load_candles().items():
        if len(candles) < 250:
            logger.info("skip %s: only %d candles", coin, len(candles))
            continue
        X, y, ts = build_dataset(candles)
        part = X.copy()
        part["__y"] = y.to_numpy()
        part["__ts"] = pd.to_datetime(ts.to_numpy(), utc=True)
        frames.append(part)
    if not frames:
        return None
    data = pd.concat(frames).sort_values("__ts").reset_index(drop=True)
    split = int(len(data) * (1 - TEST_FRACTION))
    return data.iloc[:split], data.iloc[split:]


def run() -> bool:
    """Train, evaluate out-of-sample, and promote if it beats the incumbent. Returns promoted."""
    assembled = _assemble()
    if assembled is None:
        logger.warning("no training data; aborting")
        return False
    train, test = assembled
    logger.info("dataset: %d train / %d test rows", len(train), len(test))

    booster = lgb.train(_LGB_PARAMS, lgb.Dataset(train[FEATURE_COLUMNS], label=train["__y"]),
                        num_boost_round=_NUM_ROUNDS)
    auc = _auc(test["__y"], booster.predict(test[FEATURE_COLUMNS]))
    logger.info("out-of-sample AUC: %.4f", auc)

    incumbent = db.fetch_all("SELECT oos_score FROM params WHERE kind='ml' AND is_active LIMIT 1")
    incumbent_auc = float(incumbent[0][0]) if incumbent and incumbent[0][0] is not None else None
    bar = max(MIN_AUC, incumbent_auc or 0.0)
    promote = bool(not np.isnan(auc) and auc > bar)
    logger.info("incumbent AUC %s, bar %.4f -> %s", incumbent_auc, bar,
                "PROMOTE" if promote else "keep incumbent")

    # Retrain on all data for the model we actually persist.
    full = pd.concat([train, test])
    final = lgb.train(_LGB_PARAMS, lgb.Dataset(full[FEATURE_COLUMNS], label=full["__y"]),
                      num_boost_round=_NUM_ROUNDS)
    version = "ml-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "model_str": final.model_to_string(),
        "features": FEATURE_COLUMNS,
        "auc": None if np.isnan(auc) else auc,
        "n_train": len(train),
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }

    with db.connect() as conn:
        with conn.cursor() as cur:
            if promote:
                cur.execute("UPDATE params SET is_active = false WHERE kind='ml' AND is_active")
            cur.execute(
                "INSERT INTO params (kind, version, payload, oos_score, is_active) "
                "VALUES ('ml', %s, %s, %s, %s)",
                (version, Jsonb(payload), None if np.isnan(auc) else auc, promote))
    logger.info("recorded params %s (active=%s)", version, promote)
    return promote


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except Exception:
        logger.exception("training failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
