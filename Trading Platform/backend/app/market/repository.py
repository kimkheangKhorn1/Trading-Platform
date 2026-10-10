"""Persistence for feeds, candle series and candles."""
from __future__ import annotations

import json
import time as _time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ..db import connect
from ..engine.bars import Bars
from .timeframes import TIMEFRAME_ORDER


@dataclass(frozen=True)
class Feed:
    id: str
    provider: str
    symbol: str
    instrument: str | None
    price_type: str
    display_name: str
    precision: int
    alignment: str
    meta: dict

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "symbol": self.symbol,
            "instrument": self.instrument,
            "price_type": self.price_type,
            "display_name": self.display_name,
            "precision": self.precision,
            "alignment": self.alignment,
            "meta": self.meta,
        }


@dataclass(frozen=True)
class SeriesInfo:
    id: int
    feed_id: str
    timeframe: str
    origin: str
    oldest_reached: bool
    updated_at: int


CandleRow = tuple[int, float, float, float, float, float, int]


class MarketStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @property
    def conn(self):
        return connect(self.db_path)

    # ---------------------------------------------------------------- feeds
    def ensure_feed(
        self,
        feed_id: str,
        *,
        provider: str,
        symbol: str,
        instrument: str | None,
        price_type: str,
        display_name: str,
        precision: int = 3,
        alignment: str = "ny17",
        meta: dict | None = None,
    ) -> Feed:
        c = self.conn
        c.execute(
            """INSERT INTO feeds (id, provider, symbol, instrument, price_type, display_name, precision,
                                  alignment, created_at, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,
                    precision=excluded.precision, meta=excluded.meta""",
            (feed_id, provider, symbol, instrument, price_type, display_name, precision, alignment,
             int(_time.time()), json.dumps(meta or {})),
        )
        c.commit()
        feed = self.get_feed(feed_id)
        assert feed is not None
        return feed

    def get_feed(self, feed_id: str) -> Feed | None:
        row = self.conn.execute("SELECT * FROM feeds WHERE id=?", (feed_id,)).fetchone()
        return self._feed(row) if row else None

    def list_feeds(self) -> list[Feed]:
        rows = self.conn.execute("SELECT * FROM feeds ORDER BY provider DESC, id").fetchall()
        return [self._feed(r) for r in rows]

    def delete_feed(self, feed_id: str) -> None:
        c = self.conn
        ids = [r[0] for r in c.execute("SELECT id FROM series WHERE feed_id=?", (feed_id,))]
        for sid in ids:
            c.execute("DELETE FROM candles WHERE series_id=?", (sid,))
        c.execute("DELETE FROM series WHERE feed_id=?", (feed_id,))
        c.execute("DELETE FROM feeds WHERE id=?", (feed_id,))
        c.commit()

    @staticmethod
    def _feed(row) -> Feed:
        return Feed(
            id=row["id"], provider=row["provider"], symbol=row["symbol"], instrument=row["instrument"],
            price_type=row["price_type"], display_name=row["display_name"], precision=row["precision"],
            alignment=row["alignment"], meta=json.loads(row["meta"] or "{}"),
        )

    # --------------------------------------------------------------- series
    def ensure_series(self, feed_id: str, timeframe: str, origin: str = "native") -> SeriesInfo:
        c = self.conn
        c.execute(
            "INSERT INTO series (feed_id, timeframe, origin) VALUES (?,?,?) ON CONFLICT(feed_id, timeframe) DO NOTHING",
            (feed_id, timeframe, origin),
        )
        c.commit()
        info = self.get_series(feed_id, timeframe)
        assert info is not None
        return info

    def get_series(self, feed_id: str, timeframe: str) -> SeriesInfo | None:
        row = self.conn.execute(
            "SELECT * FROM series WHERE feed_id=? AND timeframe=?", (feed_id, timeframe)
        ).fetchone()
        if not row:
            return None
        return SeriesInfo(row["id"], row["feed_id"], row["timeframe"], row["origin"],
                          bool(row["oldest_reached"]), row["updated_at"])

    def list_series(self, feed_id: str) -> list[SeriesInfo]:
        rows = self.conn.execute("SELECT * FROM series WHERE feed_id=?", (feed_id,)).fetchall()
        out = [SeriesInfo(r["id"], r["feed_id"], r["timeframe"], r["origin"], bool(r["oldest_reached"]),
                          r["updated_at"]) for r in rows]
        return sorted(out, key=lambda s: TIMEFRAME_ORDER.index(s.timeframe) if s.timeframe in TIMEFRAME_ORDER else 99)

    def set_oldest_reached(self, series_id: int, reached: bool = True) -> None:
        self.conn.execute("UPDATE series SET oldest_reached=? WHERE id=?", (int(reached), series_id))
        self.conn.commit()

    def series_stats(self, series_id: int) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n, MIN(t) AS first, MAX(t) AS last FROM candles WHERE series_id=?",
            (series_id,),
        ).fetchone()
        return {"count": row["n"], "first": row["first"], "last": row["last"]}

    # -------------------------------------------------------------- candles
    def upsert_candles(self, series_id: int, rows: Iterable[CandleRow]) -> int:
        """Insert or update candles; returns the number of rows inserted or changed."""
        c = self.conn
        before = c.total_changes
        c.executemany(
            """INSERT INTO candles (series_id, t, o, h, l, c, v, complete) VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(series_id, t) DO UPDATE SET o=excluded.o, h=excluded.h, l=excluded.l,
                   c=excluded.c, v=excluded.v, complete=excluded.complete
               WHERE candles.o IS NOT excluded.o OR candles.h IS NOT excluded.h OR candles.l IS NOT excluded.l
                  OR candles.c IS NOT excluded.c OR candles.v IS NOT excluded.v
                  OR candles.complete IS NOT excluded.complete""",
            ((series_id, int(t), float(o), float(h), float(l), float(cl), float(v), int(k))
             for t, o, h, l, cl, v, k in rows),
        )
        changed = c.total_changes - before
        c.execute("UPDATE series SET updated_at=? WHERE id=?", (int(_time.time()), series_id))
        c.commit()
        return changed

    def load_bars(
        self,
        series_id: int,
        *,
        start: int | None = None,
        end: int | None = None,
        before: int | None = None,
        limit: int | None = None,
        complete_only: bool = False,
    ) -> Bars:
        """Load candles in ascending time order.

        ``start``/``end`` are inclusive open-time bounds; ``before`` is exclusive. With ``limit`` the
        *latest* ``limit`` matching candles are returned (useful for paging backwards).
        """
        where = ["series_id=?"]
        params: list = [series_id]
        if start is not None:
            where.append("t>=?")
            params.append(int(start))
        if end is not None:
            where.append("t<=?")
            params.append(int(end))
        if before is not None:
            where.append("t<?")
            params.append(int(before))
        if complete_only:
            where.append("complete=1")
        sql = f"SELECT t,o,h,l,c,v,complete FROM candles WHERE {' AND '.join(where)}"
        if limit is not None:
            sql = f"SELECT * FROM ({sql} ORDER BY t DESC LIMIT ?) ORDER BY t ASC"
            params.append(int(limit))
        else:
            sql += " ORDER BY t ASC"
        rows = self.conn.execute(sql, params).fetchall()
        return Bars.from_rows([tuple(r) for r in rows])

    def count_before(self, series_id: int, ts: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM candles WHERE series_id=? AND t<?", (series_id, int(ts))
        ).fetchone()[0]

    def latest_time(self, series_id: int, complete_only: bool = False) -> int | None:
        sql = "SELECT MAX(t) FROM candles WHERE series_id=?" + (" AND complete=1" if complete_only else "")
        return self.conn.execute(sql, (series_id,)).fetchone()[0]

    def oldest_time(self, series_id: int) -> int | None:
        return self.conn.execute("SELECT MIN(t) FROM candles WHERE series_id=?", (series_id,)).fetchone()[0]

    def delete_candles(self, series_id: int, times: Sequence[int]) -> None:
        self.conn.executemany("DELETE FROM candles WHERE series_id=? AND t=?", ((series_id, int(t)) for t in times))
        self.conn.commit()
