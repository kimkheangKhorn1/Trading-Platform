"""Real persisted completion between a polling row read and future membership check."""
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from app.backtest.service import BacktestJobs
from app.market.repository import MarketStore


@pytest.mark.parametrize('failed', [False, True])
def test_completion_during_poll_does_not_report_interrupted(tmp_path, failed):
    store = MarketStore(tmp_path / 'jobs.db')
    jobs = BacktestJobs(store, SimpleNamespace(backtest_workers=1))
    job_id = 'poll-race'
    store.conn.execute("INSERT INTO backtests (id, created_at, status, request) VALUES (?,1,'running','{}')", (job_id,))
    store.conn.commit()
    completed = Future()
    if failed:
        completed.set_exception(RuntimeError('actual calculation failure'))
    else:
        completed.set_result({'saved': True})

    class CompletionDuringLookup(dict):
        def __contains__(self, key):
            jobs._finished(key, completed)
            return super().__contains__(key)

    jobs._running = CompletionDuringLookup({job_id: completed})
    result = jobs.get(job_id)
    assert result['status'] == ('error' if failed else 'done')
    if failed:
        assert result['error'] == 'actual calculation failure'
    else:
        assert result['result'] == {'saved': True} and result['error'] is None


def test_orphaned_running_job_still_reports_restart(tmp_path):
    store = MarketStore(tmp_path / 'jobs.db')
    jobs = BacktestJobs(store, SimpleNamespace(backtest_workers=1))
    store.conn.execute("INSERT INTO backtests (id, created_at, status, request) VALUES ('orphan',1,'running','{}')")
    store.conn.commit()
    result = jobs.get('orphan')
    assert result['status'] == 'error' and 'server restarted' in result['error']
