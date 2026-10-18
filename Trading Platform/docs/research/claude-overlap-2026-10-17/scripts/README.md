# Analysis scripts

How to run:
- Work from a scratch directory.
- `cache.py` needs the backend venv. It writes `m1t.npy`, `m1.npy`, `spr.npy` and `h1.npy` from the research `Data` loader (cutoff 2026-10-14, opening-spread proxy).
- The other scripts need numpy, and matplotlib for `render.py`. They read the runs in `../runs/`. `summ.load()` holds that path (`RUNS`); edit it if you move the folder.

The scripts, in order:
1. `summ.py baseline`: the summary.
2. `path.py baseline`: MFE before SL. Horizons are counted in M1 *rows*, not wall-clock days.
3. `missed.py baseline`: the closest approach of unfilled orders. This is a retrospective label, not an executable entry.
4. `sample.py`: the frozen chart sample, saved as `../sample.json`.
5. `render.py`: draws the PNGs into `../charts/`.

Known exposure: `path.py` and `missed.py` first printed all-year aggregates (2015–2026) before the Development-only rule was frozen. The sample itself uses Development cases only.
