"""Caches that make replay steps cheap must give exactly what a full recalculation gives, forward and on rewind."""
import copy
import random

from app.badge_overlap import BadgeOverlapService
from app.engine.bars import Bars
from app.engine.drawings import DrawingStore
from app.market.repository import MarketStore
from app.overlap_reactions import PatternTape, overlap_reactions, tape_reactions
from app.scripts.smc_master import SmcMaster
from tests.helpers import ts

T = ts('2026-10-05 00:00')


def random_bars(n, step, seed=3):
    rnd = random.Random(seed)
    rows, price = [], 2000.0
    for i in range(n):
        o = price
        c = o + rnd.uniform(-3, 3)
        rows.append((T + i * step, o, max(o, c) + rnd.uniform(0, 2), min(o, c) - rnd.uniform(0, 2), c, 1, True))
        price = c
    return Bars.from_rows(rows)


def test_pattern_tape_extended_in_pieces_and_read_as_a_prefix_matches_a_fresh_run():
    step = 300
    bars = random_bars(400, step)
    p = SmcMaster({}).p
    zones = [dict(side=side, bottom=lo, top=lo + 4, lock_id=f'{side}{k}', locked_at=T + k * step,
                  touched_at=T + (k + 5) * step + 60)
             for k in range(0, 380, 25) for side, lo in (('demand', 1996.0), ('supply', 2002.0))]
    tape = PatternTape(p, 'M5')
    for end in range(50, 401, 50):  # forward steps
        tape.extend(bars.slice(0, end), T + end * step)
        assert tape_reactions(tape, zones, 'M5', T + end * step) == overlap_reactions(bars, zones, p, 'M5', T + end * step)
    for end in (390, 200, 77):  # rewinds read a prefix
        assert tape_reactions(tape, zones, 'M5', T + end * step) == overlap_reactions(bars, zones, p, 'M5', T + end * step)


def test_fast_drawing_copy_and_json_match_the_dataclass_versions():
    from dataclasses import asdict
    d = DrawingStore()
    b = d.new_box(left=1, right=2, top=3.0, bottom=1.0, group='g')
    b._bull = True
    d.new_line(x1=1, y1=2.0, x2=3, y2=4.0)
    d.new_label(x=1, y=2.0, text='a')
    assert d.to_json() == [asdict(o) for o in d.all()]
    assert [list(x) for x in d.to_json()] == [list(asdict(o)) for o in d.all()]
    c = copy.deepcopy(d)
    assert c.to_json() == d.to_json() and all(x is not y for x, y in zip(c.all(), d.all()))
    assert c.all()[0]._bull is True


def test_indexed_lock_history_blocks_exactly_like_a_scan_of_every_earlier_lock():
    from app.locked_overlaps import LockHistory, intersects, source_ids, source_keys

    def scan(previous, band, at):  # the plain rule: an earlier lock owns the band
        ids, keys = source_ids(band), source_keys(band)
        for row, old, old_ids, _ in previous:
            retired = row['deleted_at'] is not None and row['deleted_at'] <= at
            if ids <= old_ids or (not retired and intersects(band, old)) or (retired and keys & source_keys(old)):
                return True
        return False

    rnd = random.Random(7)
    for _ in range(40):
        history, at = LockHistory(), T
        for _ in range(60):
            at += rnd.choice((0, 60, 300, 900))
            side = rnd.choice(('demand', 'supply'))
            bottom = rnd.uniform(2000, 2020)
            badges = [dict(timeframe=rnd.choice(('M1', 'M5', 'M15')), side=side, origin=T + 300 * rnd.randrange(12),
                           bottom=bottom, top=bottom + 2) for _ in range(rnd.randint(1, 3))]
            band = dict(side=side, bottom=bottom, top=bottom + rnd.uniform(0.5, 3), badges=badges)
            clock = at if rnd.random() < 0.9 else at - 1800  # also an earlier clock (rebuilds the live list)
            assert history.blocked(band, clock) == scan(history.entries, band, clock)
            if rnd.random() < 0.6:
                deleted = rnd.choice((None, at + rnd.choice((-600, 0, 600, 3000))))
                history.add(dict(deleted_at=deleted), band, source_ids(band), deleted is None)


def test_a_replay_tape_rebuilt_at_the_cursor_still_holds_everything_from_the_replay_start(tmp_path):
    store = MarketStore(tmp_path / 'tape.db')
    store.ensure_feed('f', provider='csv', symbol='XAUUSD', instrument=None, price_type='bid', display_name='Test')
    m5 = random_bars(9000, 300)
    store.upsert_candles(store.ensure_series('f', 'M5').id,
                         zip(m5.time, m5.open, m5.high, m5.low, m5.close, m5.volume, m5.complete.astype(int)))
    feed, p = store.get_feed('f'), SmcMaster({}).p
    start, end = T + 3000 * 300, T + 8900 * 300  # a replay longer than the tape's usual history
    # a fresh cache at the cursor (backend restart / settings change) without the start would miss its first trades
    assert BadgeOverlapService(store).tape(feed, 'M5', end, p).times[0] > start
    tape = BadgeOverlapService(store).tape(feed, 'M5', end, p, since=start)
    assert tape.times[0] <= start - 2999 * 300 and tape.count(start) >= 3000 and tape.times[-1] == end - 300
