"""Observed overlap locks: immutable geometry, per-band deletion and future source retirement, reversible hiding.

Only the original badge service discovers candidates. This ledger never detects D/S zones.
Birth is the observation clock, not a source's backdated base candle. Closed M1 data supplies
first-touch evidence even across skipped replay bars and while a lock is hidden.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable

from .market.repository import MarketStore

# Only settings that change D/S detection define a new analysis context. Distance, color,
# output limits and the display timeframe must not destroy an existing lock.
DETECTOR_INPUTS = ('ds_mode', 'ds_min_atr', 'ds_max_atr', 'ds_min_pc_change', 'ds_max_pc_zone', 'ds_max_zones')


def source_ids(band: dict) -> frozenset[str]:
    return frozenset(json.dumps([z['timeframe'], z['side'], z['origin'], z['bottom'], z['top']])
                     for z in band['badges'])


def source_keys(band: dict) -> frozenset[tuple]:
    """Stable source identity: changing a zone's bounds must not make it reusable."""
    return frozenset((z['timeframe'],z['side'],int(z['origin'])) for z in band['badges'])


def source_touch_boundary(band: dict) -> float:
    """The first proximal edge reached among ALL original constituent zones."""
    if band['side']=='demand':
        return max([band['top'], *(z['top'] for z in band['badges'])])
    return min([band['bottom'], *(z['bottom'] for z in band['badges'])])


def intersects(a: dict, b: dict) -> bool:
    return a['side'] == b['side'] and max(a['bottom'], b['bottom']) < min(a['top'], b['top'])


def visible_band(band: dict, low: float, high: float, distance: float) -> bool:
    if distance == 0:
        return True
    padding = (high - low) * distance
    return band['top'] >= low - padding and band['bottom'] <= high + padding


class LockHistory:
    """Accepted locks, oldest first, indexed so ``blocked`` is a few lookups instead of a scan of every earlier lock
    (a scope with ~1400 locks took ~1 s per observation, several times per replay step).

    ``blocked(band, at)`` is True when an earlier lock already owns the band: it contains all of the band's sources,
    it is still active at ``at`` and overlaps the band, or it was retired by ``at`` and shares any source with it."""

    def __init__(self):
        self.entries: list[tuple] = []  # (row, band, ids, active)
        self._by_id: dict[str, list[frozenset]] = {}  # source id -> source-id sets of the locks containing it
        self._retired_at: dict[tuple, int] = {}  # source key -> earliest deletion among the locks containing it
        self._live: list[tuple] = []  # (deleted_at, band) of locks not deleted by self._live_at
        self._live_at: int | None = None

    def __iter__(self):
        return iter(self.entries)

    def add(self, row: dict, band: dict, ids: frozenset, active: bool) -> None:
        self.entries.append((row, band, ids, active))
        for i in ids:
            self._by_id.setdefault(i, []).append(ids)
        deleted = row['deleted_at']
        if deleted is not None:
            for key in source_keys(band):
                if deleted < self._retired_at.get(key, deleted + 1):
                    self._retired_at[key] = deleted
        if deleted is None or self._live_at is None or deleted > self._live_at:
            self._live.append((deleted, band['side'], band['bottom'], band['top']))

    def blocked(self, band: dict, at: int, ids: frozenset | None = None) -> bool:
        ids = source_ids(band) if ids is None else ids
        if not ids:
            return bool(self.entries)  # the empty set is inside every earlier lock's sources
        if any(ids <= old for old in self._by_id.get(next(iter(ids)), ())):
            return True
        if any(self._retired_at.get(key, at + 1) <= at for key in source_keys(band)):
            return True
        if self._live_at is None or at < self._live_at:  # clocks only grow in practice; rebuild if not
            self._live = [(r['deleted_at'], b['side'], b['bottom'], b['top']) for r, b, _, _ in self.entries]
        if at != self._live_at:
            self._live = [live for live in self._live if live[0] is None or live[0] > at]
            self._live_at = at
        side, bottom, top = band['side'], band['bottom'], band['top']  # intersects() with a still-active lock
        return any(s == side and max(bottom, b) < min(top, t) for _, s, b, t in self._live)


class LockedOverlapRegistry:
    def __init__(self, store: MarketStore):
        self.store = store
        self._guard = threading.Lock()
        self._ready = False

    @staticmethod
    def scope(lock_id: str, feed_id: str, inputs: dict, timeframes: list[str], minimum: int) -> str:
        value = [lock_id, feed_id, {k: inputs[k] for k in DETECTOR_INPUTS}, sorted(set(timeframes)), minimum]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def observe(self, *, scope: str, feed_id: str, as_of: int, candidates: list[dict] | Callable[[frozenset], list[dict]],
                low: float, high: float, distance: float, close: float,
                show_demand: bool = True, show_supply: bool = True) -> dict:
        with self._guard:
            conn = self.store.conn
            if not self._ready:
                conn.execute('''CREATE TABLE IF NOT EXISTS overlap_locks_v1 (
                    scope TEXT NOT NULL, id TEXT NOT NULL, locked_at INTEGER NOT NULL,
                    touched_at INTEGER, checked_until INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(scope,id))''')
                columns={r['name'] for r in conn.execute('PRAGMA table_info(overlap_locks_v1)')}
                if 'deleted_at' not in columns:
                    conn.execute('ALTER TABLE overlap_locks_v1 ADD COLUMN deleted_at INTEGER')
                if 'source_checked_until' not in columns:
                    conn.execute('ALTER TABLE overlap_locks_v1 ADD COLUMN source_checked_until INTEGER NOT NULL DEFAULT 0')
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
                band = json.loads(row['payload'])
                field, price = ('l',band['top']) if band['side']=='demand' else ('h',band['bottom'])
                operator = '<=' if band['side']=='demand' else '>='
                if row['touched_at'] is None and row['checked_until'] < as_of:
                    hit = conn.execute(f"""SELECT t FROM candles WHERE series_id=? AND complete=1
                        AND t>=? AND t+60<=? AND {field}{operator}? ORDER BY t LIMIT 1""",
                        (minute_series.id,max(row['locked_at'],row['checked_until']),as_of,price)).fetchone()
                    row['touched_at'] = int(hit['t'])+60 if hit else None
                    row['checked_until'] = as_of
                    conn.execute('UPDATE overlap_locks_v1 SET touched_at=?,checked_until=? WHERE scope=? AND id=?',
                                 (row['touched_at'],as_of,scope,row['id']))
                # Repair old source-edge deletions in place. Existing groups sharing a broad
                # source retain their original geometry/count until THEIR OWN band is touched.
                if row['deleted_at'] != row['touched_at']:
                    row['deleted_at'] = row['touched_at']
                    conn.execute('UPDATE overlap_locks_v1 SET deleted_at=? WHERE scope=? AND id=?',
                                 (row['deleted_at'],scope,row['id']))

            def fold(rows):
                # Earlier observed locks own their source group. This also handles replay discovering
                # an earlier valid lock after a later snapshot was saved, without revealing future births.
                # ANY retired source forbids a mixed old/new group, on every TF (LockHistory.blocked).
                accepted = LockHistory()
                for row in rows:
                    band = json.loads(row['payload'])
                    ids = source_ids(band)
                    if accepted.blocked(band,row['locked_at'],ids):
                        continue
                    active = row['deleted_at'] is None or row['deleted_at'] > as_of
                    accepted.add(row, band, ids, active)
                return accepted

            rows = load()
            for row in rows:
                check_touch(row)
            accepted = fold(rows)
            retired = frozenset(key for _,old_band,_,active in accepted if not active for key in source_keys(old_band))
            choices = candidates(retired) if callable(candidates) else candidates
            for band in choices:
                ids = source_ids(band)
                # Consuming a group retires ALL its source origins from future combinations.
                # Only completely independent fresh source origins may form the next group.
                if accepted.blocked(band,as_of,ids):
                    continue
                # A far candidate is still locked now (hidden), so it returns with its original bounds.
                if not (close > source_touch_boundary(band) if band['side'] == 'demand' else close < source_touch_boundary(band)):
                    continue  # never register a new untouched lock already inside/beyond its proximal edge
                identity = hashlib.sha256(json.dumps(
                    [band['side'], band['bottom'], band['top'], sorted(ids)], sort_keys=True).encode()).hexdigest()
                payload = json.dumps(band, sort_keys=True)
                conn.execute('''INSERT INTO overlap_locks_v1 (scope,id,locked_at,touched_at,checked_until,payload) VALUES (?,?,?,NULL,?,?)
                    ON CONFLICT(scope,id) DO UPDATE SET locked_at=excluded.locked_at,
                    touched_at=NULL,checked_until=excluded.checked_until,deleted_at=NULL,source_checked_until=0
                    WHERE excluded.locked_at<overlap_locks_v1.locked_at''', (scope, identity, as_of, as_of, payload))
                row = dict(scope=scope, id=identity, locked_at=as_of, touched_at=None,
                           checked_until=as_of, payload=payload, deleted_at=None, source_checked_until=as_of)
                accepted.add(row, json.loads(payload), ids, True)
            conn.commit()
            # Reload/fold gives stable ordering and one owner per area across replay branches/restarts.
            accepted = fold(load())
            displayed, hidden, deleted = [], 0, 0
            reaction_zones, signal_zones = [], []
            for row, band, _, active in accepted:
                side_shown = show_demand if band['side'] == 'demand' else show_supply
                if side_shown and row['touched_at'] is not None and row['touched_at'] <= as_of:
                    zone = {**band, 'lock_id': row['id'], 'locked_at': row['locked_at'], 'touched_at': row['touched_at']}
                    # Trade signals keep every touched lock: a trade record must not vanish when price moves away.
                    signal_zones.append(zone)
                    if visible_band(band, low, high, distance):
                        reaction_zones.append(zone)
                if not active:
                    deleted += 1
                    continue
                if not side_shown or not visible_band(band, low, high, distance):
                    hidden += 1
                    continue
                displayed.append({**band, 'lock_id': row['id'], 'locked_at': row['locked_at']})
            return dict(overlaps=displayed, total_overlaps=len(displayed), active_locks=len(displayed)+hidden,
                        hidden_locks=hidden, deleted_locks=deleted, lock_mode=True, reaction_zones=reaction_zones,
                        signal_zones=signal_zones)
