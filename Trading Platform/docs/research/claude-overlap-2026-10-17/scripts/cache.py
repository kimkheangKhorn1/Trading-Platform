import sys; sys.path.insert(0,'/Users/kimkheangkhorn/Desktop/Trading Platform/backend/tools')
import numpy as np
from claude_overlap_research import Data
d=Data()
np.save('m1t.npy', d.m1t); np.save('m1.npy', d.m1); np.save('spr.npy', d.spread); np.save('h1.npy', d.h1)
