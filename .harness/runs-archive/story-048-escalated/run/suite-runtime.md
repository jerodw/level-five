# Suite wall-clock runtime, before and after the conversion

Both figures are measurements taken with `time .venv/bin/python -m pytest tests/ -q`
on this machine, not estimates.

| Point | Tree | Collected | pytest-reported | wall clock |
|---|---|---|---|---|
| Before | `dcda75d`, clean working tree (implementer stage) | 2449 passed | 474.49 s | 7:54.84 |
| After | working tree at the end of the tester stage | 2490 passed | 454.72 s | 7:34.95 |

The conversion recovered **19.9 s** of wall clock (474.49 s → 454.72 s) while
adding 41 tests, which is the effect the story predicted: a run driven by a
two- or four-stage built workflow is cheaper than the same run driven by the
deployed four-stage one, and the saving outweighs the new assertions.

An intermediate measurement, taken by the verifier on the tree after the first
three conversions, was 2488 passed in 474.10 s / 7:54.33 wall. The figure above
supersedes it.
