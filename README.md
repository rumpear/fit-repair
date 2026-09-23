# fit-cleaner

Cleans a bike computer FIT file of the damage done by GPS jamming and spoofing, so that
Strava gets the distance and speed right.

## What it fixes

- **Impossible coordinates**: a jump to another continent, a spoofed position, short outliers.
- **Frozen coordinates**: the position stands still while the wheel sensor says the bike is moving.
- **Speed spikes** (e.g. 200 km/h from spoofed GPS) and the extra distance they added.
- **Lap and session summaries**: distance, average and max speed, start and end points, the track's bounding box.

Heart rate, cadence, power, altitude, temperature and the timer are left alone. No records
are deleted: bad points only lose their coordinates. Everything else in the file, including
manufacturer data, is preserved byte for byte.

## Installation

```
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## Usage

```
fit-cleaner "Afternoon_Ride.fit"          # writes Afternoon_Ride.clean.fit next to it
fit-cleaner ride.fit --dry-run            # report only, nothing written
fit-cleaner ride.fit -o fixed.fit
```

| Option | Default | What it does |
|---|---|---|
| `--max-speed KMH` | 100 | A bike never goes faster. Threshold for both speed and GPS jumps |
| `--interpolate-gap SEC` | 30 | Removed points in holes of at most SEC seconds are filled by interpolation; `0` disables |
| `--no-sensor-check` | off | Don't cross-check GPS against the wheel sensor distance |
| `--dry-run` | off | Show the report and write nothing |

The input file is never overwritten.

## Uploading to Strava

1. **Delete the original activity on Strava** if it's already there. Strava detects
   duplicates by start time and will reject the fixed file.
2. Upload `*.clean.fit`: strava.com → "+" → "Upload activity" → "File".
3. **Don't use "Correct Distance"**. It recomputes the distance from GPS, and where GPS is
   missing there are no coordinates, so the distance will shrink.

Why this works: Strava takes the total distance, average and max speed
[from the `distance` stream in the file](https://support.strava.com/hc/en-us/articles/216919487-How-Distance-is-Calculated),
not from the coordinates. So the tool fixes exactly that stream and makes the lap and
session summaries agree with it. Where there are no coordinates, Strava simply doesn't draw
the track. If GPS drops out and comes back, Strava joins the points with a straight line.

## How it works

```
.fit → reader → speed → position → summary → writer → .clean.fit
                                                  └→ report
```

- **reader**: decodes the file with [fitdecode](https://github.com/polyvertex/fitdecode) and
  remembers where every field lives in the file.
- **speed**: finds speed above `--max-speed` and widens each spike over the surrounding
  hard acceleration and braking. Speed there is interpolated from the neighbours. Distance
  is rebuilt step by step: plausible increments stay as they are, impossible ones are
  replaced by the corrected speed times the running timer. Everything after shifts by
  itself, since the field is cumulative.
- **position**:
  1. removes frozen points: the position stays within 3 m while the wheel covers 30 m or more;
  2. splits the track into connected pieces: neighbouring points are reachable at no more
     than `--max-speed`, and a piece ends where GPS was out;
  3. scores the pieces by duration and by how well the GPS path length agrees with the
     wheel sensor;
  4. accepts pieces from best to worst, only if they are reachable from already accepted
     neighbours. The decision is made at the moment of the jump, so a spoofed position
     doesn't become "reachable" just because time has passed.
- **summary**: recomputes only the fields that depend on repaired data. Lap boundaries
  don't move.
- **writer**: rewrites values in place, recomputes the CRC and re-reads the result with
  strict checking.

## Development

```
pip install -e ".[dev]"
pytest
```

The tests build synthetic FIT files (`tests/fitbuilder.py`), so no real rides are needed in
the repository. To check against your own files:

```
set FIT_CLEANER_SAMPLE=C:\path\to\ride.fit
pytest tests/test_cli.py
```
