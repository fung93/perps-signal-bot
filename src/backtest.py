"""Backtest the rule engine + sizing over historical candles (research / calibration).

Replays the ACTUAL rules.evaluate + sizing.size on ~12 months of candles and simulates each
LONG/SHORT to its intraday outcome using the same logic grade.py uses, then reports win rate,
expectancy, and drawdown — overall, per coin, per direction. With --sweep it grids the key
parameters (entry threshold, ATR stop multiple, reward:risk) so they can be calibrated on
evidence instead of guesses.

Caveats: historical funding isn't stored, so funding is treated as neutral (it's below the
crowding threshold live anyway); fills are idealised (no fees/slippage, 1h-resolution TP/SL),
same as live grading. Active-hours is applied (faithful to live); the daily cap is a separate
risk throttle and is NOT applied here — this measures the underlying signal edge.

    python -m src.backtest
    python -m src.backtest --sweep
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config
from .data import binance
from .feedback import analytics, grade   # reuse _stats / _eod / _resolve for fidelity
from .indicators import compute
from .signals import rules, sizing

logger = logging.getLogger(__name__)

TRAIN_DAYS = 365
DATA_DIR = "data"
FEE_RATE = 0.0   # taker fee per side as a fraction of notional (set from --fee-bps)


def _load_candles() -> dict[str, pd.DataFrame]:
    """Per-coin candle history on the signal timeframe: newest backfill CSV, else fetch."""
    csvs = sorted(glob.glob(os.path.join(DATA_DIR, "candles_backfill_*.csv")))
    if csvs:
        logger.info("loading candles from %s", csvs[-1])
        df = pd.read_csv(csvs[-1])
        df = df[df["timeframe"] == config.SIGNAL_TIMEFRAME].copy()
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        return {coin: g for coin, g in df.groupby("coin")}
    logger.info("no backfill CSV; fetching %d days", TRAIN_DAYS)
    start = datetime.now(timezone.utc) - timedelta(days=TRAIN_DAYS)
    out: dict[str, pd.DataFrame] = {}
    for coin in config.COINS:
        candles = binance.fetch_range(coin, config.SIGNAL_TIMEFRAME, start)
        out[coin] = pd.DataFrame([
            {"open_time": c.open_time, "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume} for c in candles])
    return out


def _prep(df: pd.DataFrame) -> dict:
    """Precompute the per-coin arrays a pass needs (features computed once, reused in sweeps)."""
    df = df.sort_values("open_time").reset_index(drop=True)
    feats = compute.compute_features(df)

    def col(frame, name):
        return [None if pd.isna(v) else float(v) for v in frame[name]]

    return {
        "open_time": df["open_time"].tolist(),
        "high": df["high"].astype(float).tolist(),
        "low": df["low"].astype(float).tolist(),
        "close": df["close"].astype(float).tolist(),
        "rsi": col(feats, "rsi"), "ema_fast": col(feats, "ema_fast"),
        "ema_slow": col(feats, "ema_slow"), "ema_long": col(feats, "ema_long"),
        "macd": col(feats, "macd"), "macd_signal": col(feats, "macd_signal"),
        "atr": col(feats, "atr"), "vol_z": col(feats, "vol_z"),
    }


def _run_coin(coin: str, p: dict) -> list[dict]:
    """Replay rules + sizing over one prepped coin series. Returns simulated trade dicts."""
    n = len(p["open_time"])
    trades: list[dict] = []
    for i in range(n):
        ts = p["open_time"][i]
        if not config.in_active_hours(ts):
            continue
        snap = rules.Snapshot(
            close=p["close"][i], ema_fast=p["ema_fast"][i], ema_slow=p["ema_slow"][i],
            ema_long=p["ema_long"][i], rsi=p["rsi"][i], macd=p["macd"][i],
            macd_signal=p["macd_signal"][i], vol_z=p["vol_z"][i], funding=None,
        )
        decision = rules.evaluate(snap)
        if decision.direction == rules.FLAT:
            continue
        sized = sizing.size(decision.direction, p["close"][i], p["atr"][i])
        if sized.rejected:
            continue

        eod = grade._eod(ts)
        fwd = []
        for j in range(i + 1, n):
            if p["open_time"][j] >= eod:
                break
            fwd.append((p["high"][j], p["low"][j], p["close"][j]))
        if not fwd:
            continue  # no room to play out before the active day closes

        _status, exit_price = grade._resolve(
            decision.direction, sized.entry, sized.take_profit, sized.stop_loss, fwd, True)
        entry, sl = sized.entry, sized.stop_loss
        if decision.direction == rules.LONG:
            pnl_pct, r = (exit_price - entry) / entry, (exit_price - entry) / (entry - sl)
        else:
            pnl_pct, r = (entry - exit_price) / entry, (entry - exit_price) / (sl - entry)
        notional = config.MARGIN_USD * sized.leverage
        stop_frac = abs(entry - sl) / entry
        round_trip = 2 * FEE_RATE                       # fees: entry + exit
        net_pnl = notional * pnl_pct - round_trip * notional
        net_r = r - (round_trip / stop_frac if stop_frac else 0.0)
        trades.append({"ts": ts, "coin": coin, "direction": decision.direction,
                       "pnl": net_pnl, "r": net_r})
    return trades


def run(prepped: dict[str, dict]) -> list[dict]:
    trades: list[dict] = []
    for coin, p in prepped.items():
        trades.extend(_run_coin(coin, p))
    trades.sort(key=lambda t: t["ts"])
    return trades


def _report(trades: list[dict]) -> None:
    logger.info("OVERALL: %s", analytics._stats([t["pnl"] for t in trades], [t["r"] for t in trades]))
    for key in ("coin", "direction"):
        groups: dict[str, list[dict]] = {}
        for t in trades:
            groups.setdefault(t[key], []).append(t)
        for g, ts in sorted(groups.items()):
            logger.info("  %s=%s: %s", key, g,
                        analytics._stats([t["pnl"] for t in ts], [t["r"] for t in ts]))


def sweep(prepped: dict[str, dict]) -> None:
    logger.info("sweep (entry threshold, ATR mult, reward:risk):")
    best = None
    for thr in (2.0, 2.5, 3.0):
        for atr_m in (1.0, 1.5, 2.0):
            for rr in (1.5, 2.0, 3.0):
                rules.ENTER_THRESHOLD, sizing.ATR_MULT, sizing.REWARD_RISK = thr, atr_m, rr
                st = analytics._stats(*_pnl_r(run(prepped)))
                logger.info("  thr=%.1f atr=%.1f rr=%.1f -> n=%d win=%.0f%% expR=%+.3f tot=%.0f",
                            thr, atr_m, rr, st["trades"], (st["win_rate"] or 0) * 100,
                            st["expectancy_r"] or 0.0, st["total_usd"])
                score = st["expectancy_r"] if st["trades"] >= 30 else None
                if score is not None and (best is None or score > best[0]):
                    best = (score, thr, atr_m, rr, st)
    if best:
        logger.info("BEST by expectancy R (>=30 trades): thr=%.1f atr=%.1f rr=%.1f -> %s",
                    best[1], best[2], best[3], best[4])


def _pnl_r(trades):
    return [t["pnl"] for t in trades], [t["r"] for t in trades]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Backtest the rule engine over candle history.")
    parser.add_argument("--sweep", action="store_true", help="grid-search key parameters")
    parser.add_argument("--fee-bps", type=float, default=5.0,
                        help="taker fee per side in basis points (default 5; use 0 for idealised)")
    args = parser.parse_args()
    global FEE_RATE
    FEE_RATE = args.fee_bps / 10000.0
    try:
        prepped = {c: _prep(df) for c, df in _load_candles().items() if len(df) >= 250}
        if args.sweep:
            sweep(prepped)
        else:
            logger.info("current params: thr=%.1f atr=%.1f rr=%.1f | fee=%.1f bps/side",
                        rules.ENTER_THRESHOLD, sizing.ATR_MULT, sizing.REWARD_RISK, args.fee_bps)
            _report(run(prepped))
    except Exception:
        logger.exception("backtest failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
