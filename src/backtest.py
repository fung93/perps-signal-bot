"""Backtest + selectivity research for the rule engine.

Replays the real rules + sizing + grading over ~12 months of history with optional regime
filters, and reports FEE-AWARE win rate / expectancy / drawdown. The plain rules are
break-even after fees; the point here is to find a *selective* configuration whose edge
clears the cost hurdle, and to confirm it out-of-sample (walk-forward) rather than in-sample.

Filters:
  * longs_only  — drop shorts (they were net-negative)
  * htf_align   — only take a 1h signal when the 4h EMA stack agrees (trend, not chop)
  * adx_min     — only trade when ADX(14) trend strength exceeds a threshold
  * atr_mult /
    reward_risk — stop distance and target multiple (live defaults 1.5xATR, 2R)
  * max_per_bar — cap same-direction entries sharing one timestamp. BTC/ETH/SOL move
                  together, so three simultaneous LONGs are one bet at 3x size, not three
                  independent ones (live: 2026-09-03 23:00 fired three LONGs, all stopped out)

Caveats: historical funding not stored (neutral); idealised fills + a flat per-side fee
(slippage on Katana's thin book could be worse). Active-hours applied; daily cap is not.

    python -m src.backtest                 # current rules, fee-aware
    python -m src.backtest --compare       # compare filter configs
    python -m src.backtest --stops         # stop-width / reward-risk grid
    python -m src.backtest --correlation   # effect of capping simultaneous correlated entries
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
    atr_mult: float | None = None       # stop distance; None -> sizing.ATR_MULT (1.5)
    reward_risk: float | None = None    # target multiple; None -> sizing.REWARD_RISK (2.0)
    max_per_bar: int | None = None      # cap same-direction entries sharing one timestamp


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


# ADX now lives in indicators/compute.adx (shared with the live pipeline).


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
        "adx": col(feats, "adx"),
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


def _cap_correlated(trades: list[dict], cap: int) -> list[dict]:
    """Keep at most ``cap`` same-direction entries per timestamp (trades must be ts-sorted).

    BTC/ETH/SOL are highly correlated, so simultaneous same-direction entries are really one
    position at N times the size. Keeps the first (coin order), matching how the live daily
    cap fills.
    """
    seen: dict[tuple, int] = {}
    kept: list[dict] = []
    for t in trades:
        key = (t["ts"], t["direction"])
        if seen.get(key, 0) >= cap:
            continue
        seen[key] = seen.get(key, 0) + 1
        kept.append(t)
    return kept


def run(prepped: dict[str, dict], f: Filters) -> list[dict]:
    """Replay every coin under ``f``. Stop/target overrides are applied around the run.

    ``sizing.size`` reads ATR_MULT / REWARD_RISK as module globals, so they are patched for
    the duration and always restored — otherwise one config would leak into the next.
    """
    prev_mult, prev_rr = sizing.ATR_MULT, sizing.REWARD_RISK
    if f.atr_mult is not None:
        sizing.ATR_MULT = f.atr_mult
    if f.reward_risk is not None:
        sizing.REWARD_RISK = f.reward_risk
    try:
        trades: list[dict] = []
        for coin, p in prepped.items():
            trades.extend(_run_coin(coin, p, f))
    finally:
        sizing.ATR_MULT, sizing.REWARD_RISK = prev_mult, prev_rr

    trades.sort(key=lambda t: t["ts"])
    if f.max_per_bar is not None:
        trades = _cap_correlated(trades, f.max_per_bar)
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
    # Live-record hypotheses (Sep 2026): shorts are the whole loss, stops look too tight,
    # and simultaneous BTC/ETH/SOL entries are one bet at 3x size.
    Filters("longs+adx>=30 1/bar", longs_only=True, adx_min=30, max_per_bar=1),
    Filters("longs+adx>=30 atr2.5", longs_only=True, adx_min=30, atr_mult=2.5),
    Filters("longs+adx>=30 atr2.5 1/bar", longs_only=True, adx_min=30,
            atr_mult=2.5, max_per_bar=1),
    # Candidates that beat the live config in-sample — each must survive walk-forward.
    Filters("adx>=30 2/bar", adx_min=30, max_per_bar=2),
    Filters("adx>=30 atr1.0 3R", adx_min=30, atr_mult=1.0, reward_risk=3.0),
    Filters("adx>=30 2/bar atr1.0 3R", adx_min=30, max_per_bar=2,
            atr_mult=1.0, reward_risk=3.0),
]

_BY_NAME = {f.name: f for f in _CONFIGS}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Backtest / selectivity research.")
    parser.add_argument("--compare", action="store_true", help="compare regime-filter configs")
    parser.add_argument("--stops", action="store_true", help="stop-width / reward-risk grid")
    parser.add_argument("--correlation", action="store_true",
                        help="effect of capping simultaneous same-direction entries")
    parser.add_argument("--walkforward", metavar="CONFIG", nargs="?", const="adx>=30",
                        help="H1 vs H2 for a named config (default adx>=30)")
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
        elif args.stops:
            # Base is the live config (adx>=30, both directions) — the comparison winner.
            logger.info("stop-width / target grid (base: adx>=30, live default = x1.5 -> 2.0R):")
            for mult in (1.0, 1.5, 2.0, 2.5, 3.0):
                for rr in (1.5, 2.0, 3.0):
                    f = Filters(f"atr x{mult} -> {rr}R", adx_min=30,
                                atr_mult=mult, reward_risk=rr)
                    _line(f.name, run(prepped, f))
        elif args.correlation:
            logger.info("correlated-exposure cap (base: adx>=30):")
            for cap in (None, 1, 2):
                f = Filters(f"max {cap or 'inf'} per bar", adx_min=30, max_per_bar=cap)
                _line(f.name, run(prepped, f))
        elif args.walkforward:
            f = _BY_NAME.get(args.walkforward)
            if f is None:
                logger.error("unknown config %r; known: %s",
                             args.walkforward, ", ".join(_BY_NAME))
                return 1
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
