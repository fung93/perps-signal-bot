"""Load the active LightGBM model from params and score live snapshots (Phase 4).

Returns None whenever there is no active model, so callers degrade gracefully to rules-only.
"""
from __future__ import annotations

import logging

import lightgbm as lgb
import numpy as np

from ... import db
from .features import engineer_snapshot

logger = logging.getLogger(__name__)

_ACTIVE = "SELECT version, payload FROM params WHERE kind = 'ml' AND is_active LIMIT 1"
_UNSET = object()
_cache = _UNSET   # caches the loaded model (or None) for the process


def load_active():
    """Return (version, booster, feature_list) for the active model, or None. Cached."""
    global _cache
    if _cache is not _UNSET:
        return _cache
    rows = db.fetch_all(_ACTIVE)
    if not rows:
        _cache = None
    else:
        version, payload = rows[0]
        _cache = (version, lgb.Booster(model_str=payload["model_str"]), payload["features"])
    return _cache


def long_proba(snap, atr) -> float | None:
    """P(long profitable within day) for a snapshot, or None if no model / missing inputs."""
    active = load_active()
    if active is None:
        return None
    _version, booster, _features = active
    vec = engineer_snapshot(snap, atr)
    if vec is None:
        return None
    return float(booster.predict(np.array([vec]))[0])
