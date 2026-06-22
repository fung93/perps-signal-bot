"""Backtest + selectivity research for the rule engine.

Replays the real rules + sizing + grading over ~12 months of history with optional regime
filters, and reports FEE-AWARE win rate / expectancy / drawdown. The plain rules are
break-even after fees; the point here is to find a *selective* configuration whose edge
clears the cost hurdle, and to confirm it out-of-sample (walk-forward) rather than in-sample.

Filters:
  * longs_only  — drop shorts (they were net-negative)
  * htf_align   — only take a 1h signal when the 4h EMA stack agrees (trend, not chop)
  * adx_min     — only trade when ADX(14) trend strength exceeds a threshold

Caveats: historical funding not stored (neutral); idealised fills + a flat per-side fee
(slippage on Katana's thin book could be worse). Active-hours applied; daily cap is not.

    python -m src.backtest                 # current rules, fee-aware
    python -m src.backtest --compare       # compare filter configs
    python -m src.backtest --walkforward   # first-half vs second-half on the selective config
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config
from .data import binance
from .feedback import analytics, grade
from .indicators import compute
from .signals import rules, sizing

logger = logging.getLogger(__name__)

TRAIN_DAYS = 365
DATA_DIR = "data"
FEE_RATE = 0.0005   # taker fee per side as a fraction of notional (5 bps; set via --fee-bps)


@dataclass(frozen=True)
class Filters:
    name: str = "baseline"
    longs_only: bool = False
    htf_align: bool = False
    adx_min: float | None = None


def _load_all() -> dict[str, dict[str, pd.DataFrame]]:
    """Per-coin {timeframe: df} for 1h (signals) and 4h (higher-timeframe trend)."""
    csvs = sorted(glob.glob(os.path.join(DATA_DIR, "candles_backfill_*.csv")))
    if csvs:
        logger.info("loading candles from %s", csvs[-1])
        df = pd.read_csv(csvs[-1])
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        return {coin: {tf: g[g["timeframe"] == tf].copy() for tf in ("1h", "4h")}
                for coin, g in df.groupby("coin")}
    logger.info("no backfill CSV; fetching %d days", TRAIN_DAYS)
    start = datetime.now(timezone.utc) - timedelta(days=TRAIN_DAYS)
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for coin in config.COINS:
        out[coin] = {}
        for tf in ("1h", "4h"):
            candles = binance.fetch_range(coin, tf, start)
            out[coin][tf] = pd.DataFrame([
                {"open_time": c.open_time, "open": c.open, "high": c.high,
                 "low": c.low, "close": c.close, "volume": c.volume} for c in candles])
    return out


def _adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Wilder ADX(14) — trend strength."""
    high, low, close = df["high"], df["low"], df["close"]
    up, down = high.diff(), -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    tr = pd.concat([(high - low), (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / length, adjust=False).mean()


def _htf_regime(df4h: pd.DataFrame) -> list[tuple]:
    """(open_time, regime) per 4h candle from its EMA stack: bull / bear / range."""
    df4h = df4h.sort_values("open_time").reset_index(drop=True)
    ef = compute.ema(df4h["close"], compute.EMA_FAST)
    es = compute.ema(df4h["close"], compute.EMA_SLOW)
    el = compute.ema(df4h["close"], compute.EMA_LONG)
    out = []
    for i in range(len(df4h)):
        a, b, c = ef.iloc[i], es.iloc[i], el.iloc[i]
        if pd.isna(a) or pd.isna(b) or pd.isna(c):
            reg = "range"
        elif a > b > c:
            reg = "bull"
        elif a < b < c:
            reg = "bear"
        else:
            reg = "range"
        out.append((df4h["open_time"].iloc[i], reg))
    return out


def _prep(df1h: pd.DataFrame, df4h: pd.DataFrame) -> dict:
    """Precompute per-coin arrays: 1h features, ADX, and the 4h regime aligned to 1h time."""
    df = df1h.sort_values("open_time").reset_index(drop=True)
    feats = compute.compute_features(df)
    adx = _adx(df)

    regimes = _htf_regime(df4h)
    htf, j, cur = [], 0, "range"
    for t in df["open_time"]:
        while j < len(regimes) and regimes[j][0] <= t:
            cur = regimes[j][1]
            j += 1
        htf.append(cur)

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
        "adx": [None if pd.isna(v) else float(v) for v in adx],
        "htf": htf,
    }


def _run_coin(coin: str, p: dict, f: Filters) -> list[dict]:
    n = len(p["open_time"])
    trades: list[dict] = []
    for i in range(n):
        ts = p["open_time"][i]
        if not config.in_active_hours(ts):
            continue
        snap = rules.Snapshot(
            close=p["close"][i], ema_fast=p["ema_fast"][i], ema_slow=p["ema_slow"][i],
            ema_long=p["ema_long"][i], rsi=p["rsi"][i], macd=p["macd"][i],
            macd_signal=p["macd_signal"][i], vol_z=p["vol_z"][i], funding=None)
        decision = rules.evaluate(snap)
        if decision.direction == rules.FLAT:
            continue

        # --- regime filters ---
        if f.longs_only and decision.direction == rules.SHORT:
            continue
        if f.htf_align:
            reg = p["htf"][i]
            if (decision.direction == rules.LONG and reg != "bull") or \
               (decision.direction == rules.SHORT and reg != "bear"):
                continue
        if f.adx_min is not None and (p["adx"][i] is None or p["adx"][i] < f.adx_min):
            continue

        sized = sizing.size(decision.direction, p["close"][i], p["atr"][i])
        if sized.rejected:
            continue
        eod = grade._eod(ts)
        fwd = []
        for k in range(i + 1, n):
            if p["open_time"][k] >= eod:
                break
            fwd.append((p["high"][k], p["low"][k], p["close"][k]))
        if not fwd:
            continue
        _status, exit_price = grade._resolve(
            decision.direction, sized.entry, sized.take_profit, sized.stop_loss, fwd, True)
        entry, sl = sized.entry, sized.stop_loss
        if decision.direction == rules.LONG:
            pnl_pct, r = (exit_price - entry) / entry, (exit_price - entry) / (entry - sl)
        else:
            pnl_pct, r = (entry - exit_price) / entry, (entry - exit_price) / (sl - entry)
        notional = config.MARGIN_USD * sized.leverage
        stop_frac = abs(entry - sl) / entry
        net_pnl = notional * pnl_pct - 2 * FEE_RATE * notional
        net_r = r - (2 * FEE_RATE / stop_frac if stop_frac else 0.0)
        trades.append({"ts": ts, "coin": coin, "direction": decision.direction,
                       "pnl": net_pnl, "r": net_r})
    return trades


def run(prepped: dict[str, dict], f: Filters) -> list[dict]:
    trades: list[dict] = []
    for coin, p in prepped.items():
        trades.extend(_run_coin(coin, p, f))
    trades.sort(key=lambda t: t["ts"])
    return trades


def _pnl_r(trades):
    return [t["pnl"] for t in trades], [t["r"] for t in trades]


def _line(label: str, trades: list[dict]) -> None:
    st = analytics._stats(*_pnl_r(trades))
    logger.info("  %-22s n=%-5d win=%3.0f%% expR=%+.3f tot=%+.0f maxDD=%.0f", label,
                st["trades"], (st["win_rate"] or 0) * 100, st["expectancy_r"] or 0.0,
                st["total_usd"], st["max_drawdown_usd"])


_CONFIGS = [
    Filters("baseline"),
    Filters("longs_only", longs_only=True),
    Filters("adx>=25", adx_min=25),
    Filters("adx>=30", adx_min=30),
    Filters("longs+adx>=20", longs_only=True, adx_min=20),
    Filters("longs+adx>=25", longs_only=True, adx_min=25),
    Filters("longs+adx>=30", longs_only=True, adx_min=30),
    Filters("htf_align (for ref)", htf_align=True),
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Backtest / selectivity research.")
    parser.add_argument("--compare", action="store_true", help="compare regime-filter configs")
    parser.add_argument("--walkforward", action="store_true", help="H1 vs H2 on the selective config")
    parser.add_argument("--fee-bps", type=float, default=5.0, help="fee per side, bps (default 5)")
    args = parser.parse_args()
    global FEE_RATE
    FEE_RATE = args.fee_bps / 10000.0
    try:
        prepped = {c: _prep(tfs["1h"], tfs["4h"]) for c, tfs in _load_all().items()
                   if len(tfs["1h"]) >= 250 and len(tfs["4h"]) >= 250}
        logger.info("fee=%.1f bps/side", args.fee_bps)

        if args.compare:
            logger.info("filter comparison (fee-aware):")
            for f in _CONFIGS:
                _line(f.name, run(prepped, f))
        elif args.walkforward:
            f = Filters("adx>=30", adx_min=30)
            trades = run(prepped, f)
            if trades:
                mid = trades[len(trades) // 2]["ts"]
                logger.info("walk-forward on %s (split %s):", f.name, mid)
                _line("H1 (in-sample)", [t for t in trades if t["ts"] < mid])
                _line("H2 (out-of-sample)", [t for t in trades if t["ts"] >= mid])
        else:
            _line("current rules", run(prepped, Filters()))
    except Exception:
        logger.exception("backtest failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
