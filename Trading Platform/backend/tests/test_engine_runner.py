import numpy as np
import pytest

from app.engine.bars import Bars
from app.engine.inputs import InputError
from app.engine.runner import DataRevisionError, ScriptRunner
from app.engine.script import Series
from app.scripts.smc_master import SmcMaster
from tests.helpers import random_walk_bars

# chart timeframe only, so no higher-timeframe data provider is needed
NO_HTF = {"show_pdhl": False, "show_pwhl": False, "show_pmhl": False}


def test_committed_bar_revision_detected():
    bars = random_walk_bars(300, seed=4)
    r = ScriptRunner(SmcMaster, NO_HTF, timeframe="M5")
    r.process(bars)
    cl = bars.close.copy()
    cl[-1] += 1
    with pytest.raises(DataRevisionError):
        r.process(Bars(bars.time, bars.open, np.maximum(bars.high, cl), bars.low, cl, bars.volume, bars.complete))


def test_inputs_validated():
    with pytest.raises(InputError):
        SmcMaster({"length": 5})  # below minval
    with pytest.raises(InputError):
        SmcMaster({"nonexistent": 1})
    with pytest.raises(InputError):
        SmcMaster({"mode": "Future"})  # not one of the options
    assert SmcMaster({"length": 30}).p["length"] == 30


def test_series_history_semantics():
    s = Series()
    for v in (1, 2, 3):
        s.set(v)
        s.commit()
    s.set(4)
    assert s[0] == 4 and s[1] == 3 and s[3] == 1 and np.isnan(s[4])
    with pytest.raises(IndexError):
        s[-1]
