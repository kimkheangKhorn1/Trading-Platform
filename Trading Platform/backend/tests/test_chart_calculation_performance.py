"""Performance changes must preserve provisional rollback and exact indicator outputs."""
import copy
import json
from dataclasses import replace

from app.engine.runner import ScriptRunner
from app.scripts.smc_master import SmcMaster
from tests.helpers import random_walk_bars

NO_HTF = {'show_pdhl': False, 'show_pwhl': False, 'show_pmhl': False}


def recursive_copy(self, memo):
    """The ordinary deepcopy path used before the scalar-history optimization."""
    clone = type(self).__new__(type(self))
    memo[id(self)] = clone
    clone.__dict__.update(copy.deepcopy(self.__dict__, memo))
    return clone


def test_fast_copy_isolates_history_and_preserves_shared_mutable_references():
    bars = random_walk_bars(1100, seed=12)
    runner = ScriptRunner(SmcMaster, NO_HTF, timeframe='M5')
    runner.process(bars)
    script = runner.script
    script.history_alias = script.rows
    fast_script, fast_drawings = copy.deepcopy((script, runner.drawings))
    assert fast_script.rows is not script.rows
    assert fast_script.rows[0] is script.rows[0]
    assert fast_script.history_alias is fast_script.rows
    for name in SmcMaster._SCALAR_HISTORY:
        copied = getattr(fast_script, name)
        original = getattr(script, name)
        assert copied is not original and len(copied) == len(original)
        copied.append(0)
        assert len(copied) == len(original) + 1
    for side in fast_script.ds:
        for zone in side:
            if zone.box is not None:
                assert zone.box is fast_drawings._bucket(zone.box)[0][zone.box.id]
                assert zone.box is not runner.drawings._bucket(zone.box)[0][zone.box.id]


def test_fast_copy_matches_recursive_copy_through_provisional_updates(monkeypatch):
    bars = random_walk_bars(1250, seed=23)
    def evaluate():
        runner = ScriptRunner(SmcMaster, NO_HTF, timeframe='M5')
        snapshots = []
        for n, provisional in ((1100, False), (1101, True), (1101, True), (1101, False), (1110, True), (1250, False)):
            part = replace(bars.slice(0, n), complete=bars.complete[:n].copy())
            if provisional:
                part.complete[-1] = False
            runner.process(part)
            snapshots.append(json.dumps(runner.output(), sort_keys=True))
        return snapshots
    fast = evaluate()
    monkeypatch.setattr(SmcMaster, '__deepcopy__', recursive_copy)
    assert evaluate() == fast


def test_security_refresh_loads_each_timeframe_once_and_reloads_next_observation():
    from app.engine.bars import Bars
    class Provider:
        def __init__(self): self.calls = []
        def load(self, timeframe, start, end, warmup_bars):
            self.calls.append(timeframe)
            return Bars.empty()
    provider = Provider()
    runner = ScriptRunner(SmcMaster, {}, timeframe='M5', htf_provider=provider)
    bars = random_walk_bars(20, seed=31)
    runner.process(bars.slice(0, 10))
    assert sorted(provider.calls) == ['D', 'M', 'W']
    runner.process(bars)
    assert sorted(provider.calls) == ['D', 'D', 'M', 'M', 'W', 'W']
