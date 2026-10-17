"""Observed overlap locks: immutable geometry, independent first-touch lifecycle, reversible hiding.

Only the original badge service discovers candidates. This ledger never detects D/S zones.
Birth is the observation clock, not a source's backdated base candle. Closed M1 data supplies
first-touch evidence even across skipped replay bars and while a lock is hidden.
"""
from __future__ import annotations

import hashlib
import json
import threading

from .market.repository import MarketStore

# Only settings that change D/S detection define a new analysis context. Distance, color,
# output limits and the display timeframe must not destroy an existing lock.
DETECTOR_INPUTS = ('ds_mode', 'ds_min_atr', 'ds_max_atr', 'ds_min_pc_change', 'ds_max_pc_zone', 'ds_max_zones')


def source_ids(band: dict) -> frozenset[str]:
    return frozenset(json.dumps([z['timeframe'], z['side'], z['origin'], z['bottom'], z['top']])
                     for z in band['badges'])


def intersects(a: dict, b: dict) -> bool:
    return a['side'] == b['side'] and max(a['bottom'], b['bottom']) < min(a['top'], b['top'])


def visible_band(band: dict, low: float, high: float, distance: float) -> bool:
    if distance == 0:
        return True
    padding = (high - low) * distance
    return band['top'] >= low - padding and band['bottom'] <= high + padding


class LockedOverlapRegistry:
    def __init__(self, store: MarketStore):
        self.store = store
        self._guard = threading.Lock()
        self._ready = False

    @staticmethod
    def scope(lock_id: str, feed_id: str, inputs: dict, timeframes: list[str], minimum: int) -> str:
        value = [lock_id, feed_id, {k: inputs[k] for k in DETECTOR_INPUTS}, sorted(set(timeframes)), minimum]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def observe(self, *, scope: str, feed_id: str, as_of: int, candidates: list[dict],
                low: float, high: float, distance: float, close: float,
                show_demand: bool = True, show_supply: bool = True) -> dict:
        with self._guard:
            conn = self.store.conn
            if not self._ready:
                conn.execute('''CREATE TABLE IF NOT EXISTS overlap_locks_v1 (
                    scope TEXT NOT NULL, id TEXT NOT NULL, locked_at INTEGER NOT NULL,
                    touched_at INTEGER, checked_until INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(scope,id))''')
                conn.commit()
                self._ready = True
            minute_series = self.store.get_series(feed_id, 'M1')
            if minute_series is None:
                raise ValueError('Locked overlaps need M1 data for first-touch tracking')

            def load():
                return [dict(row) for row in conn.execute(
                    'SELECT * FROM overlap_locks_v1 WHERE scope=? AND locked_at<=? ORDER BY locked_at,id',
                    (scope, as_of)).fetchall()]

            def check_touch(row):
                if row['touched_at'] is not None or row['checked_until'] >= as_of:
                    return
                band = json.loads(row['payload'])
                field, price = ('l', band['top']) if band['side'] == 'demand' else ('h', band['bottom'])
                operator = '<=' if band['side'] == 'demand' else '>='
                # A gap through the proximal edge also consumes a lock. No final HTF OHLC is read.
                hit = conn.execute(f'''SELECT t FROM candles WHERE series_id=? AND complete=1
                    AND t>=? AND t+60<=? AND {field}{operator}? ORDER BY t LIMIT 1''',
                    (minute_series.id, max(row['locked_at'], row['checked_until']), as_of, price)).fetchone()
                row['touched_at'] = int(hit['t']) + 60 if hit else None
                row['checked_until'] = as_of
                conn.execute('UPDATE overlap_locks_v1 SET touched_at=?,checked_until=? WHERE scope=? AND id=?',
                             (row['touched_at'], as_of, scope, row['id']))

            def fold(rows):
                # Earlier observed locks own their source group. This also handles replay discovering
                # an earlier valid lock after a later snapshot was saved, without revealing future births.
                accepted = []
                for row in rows:
                    band = json.loads(row['payload'])
                    ids = source_ids(band)
                    blocked = any(ids <= old_ids or (old_active and intersects(band, old_band))
                                  for _, old_band, old_ids, old_active in accepted)
                    if blocked:
                        continue
                    active = row['touched_at'] is None or row['touched_at'] > as_of
                    accepted.append((row, band, ids, active))
                return accepted

            rows = load()
            for row in rows:
                check_touch(row)
            accepted = fold(rows)
            for band in candidates:
                ids = source_ids(band)
                # Do not shrink/replace an existing area, or resurrect a touched lock with a
                # reduced subset of its original sources. New independent D/S origins may form new locks.
                if any(ids <= old_ids or (active and intersects(band, old_band))
                       for _, old_band, old_ids, active in accepted):
                    continue
                # A far candidate is still locked now (hidden), so it returns with its original bounds.
                if not (close > band['top'] if band['side'] == 'demand' else close < band['bottom']):
                    continue  # never register a new untouched lock already inside/beyond its proximal edge
                identity = hashlib.sha256(json.dumps(
                    [band['side'], band['bottom'], band['top'], sorted(ids)], sort_keys=True).encode()).hexdigest()
                payload = json.dumps(band, sort_keys=True)
                conn.execute('''INSERT INTO overlap_locks_v1 VALUES (?,?,?,NULL,?,?)
                    ON CONFLICT(scope,id) DO UPDATE SET locked_at=excluded.locked_at,
                    touched_at=NULL,checked_until=excluded.checked_until
                    WHERE excluded.locked_at<overlap_locks_v1.locked_at''', (scope, identity, as_of, as_of, payload))
                row = dict(scope=scope, id=identity, locked_at=as_of, touched_at=None,
                           checked_until=as_of, payload=payload)
                accepted.append((row, json.loads(payload), ids, True))
            conn.commit()
            # Reload/fold gives stable ordering and one owner per area across replay branches/restarts.
            accepted = fold(load())
            displayed, hidden, deleted = [], 0, 0
            for row, band, _, active in accepted:
                if not active:
                    deleted += 1
                    continue
                side_shown = show_demand if band['side'] == 'demand' else show_supply
                if not side_shown or not visible_band(band, low, high, distance):
                    hidden += 1
                    continue
                displayed.append({**band, 'lock_id': row['id'], 'locked_at': row['locked_at']})
            return dict(overlaps=displayed, total_overlaps=len(displayed), active_locks=len(displayed)+hidden,
                        hidden_locks=hidden, deleted_locks=deleted, lock_mode=True)
