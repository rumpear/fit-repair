# fit-repair

Repairs bike computer FIT files before they go to Strava:

- `fit-clean` removes the damage done by GPS jamming and spoofing, so that Strava gets the
  distance and speed right;
- `fit-merge` joins a ride that was split into several recordings.

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
fit-clean "Afternoon_Ride.fit"          # writes Afternoon_Ride.clean.fit next to it
fit-clean ride.fit --dry-run            # report only, nothing written
fit-clean ride.fit -o fixed.fit
```

| Option | Default | What it does |
|---|---|---|
| `--max-speed KMH` | 100 | A bike never goes faster. Threshold for both speed and GPS jumps |
| `--interpolate-gap SEC` | 30 | Removed points in holes of at most SEC seconds are filled by interpolation; `0` disables |
| `--no-sensor-check` | off | Don't cross-check GPS against the wheel sensor distance |
| `--dry-run` | off | Show the report and write nothing |

The input file is never overwritten.

## Merging recordings

If a ride was stopped by accident and started again, `fit-merge` joins the recordings into
one activity. Merge the raw files, then clean the result once, so the cleaner sees the
whole track.

```
fit-merge "Afternoon_Ride (2).fit" "Afternoon_Ride (3).fit"   # writes "Afternoon_Ride (2).merged.fit"
fit-clean "Afternoon_Ride (2).merged.fit"                      # and "Afternoon_Ride (2).merged.clean.fit"
```

| Option | Default | What it does |
|---|---|---|
| `-o FILE` | `<earliest>.merged.fit` | Where to save |
| `--bridge-gap` | off | Add the straight-line distance between recordings to the distance |
| `--force` | off | Merge even if the recordings are more than 2 h apart or the sport differs |
| `--dry-run` | off | Show the report and write nothing |

The order of files on the command line doesn't matter: they are sorted by time. Files that
overlap in time are not merged.

- **The break between recordings** becomes a pause: the timer is stopped and the distance
  doesn't grow. The end of recording in every file but the last turns into a regular pause.
- **Distance** of each following recording continues from where the previous one ended.
- **Laps** stay as recorded and are numbered consecutively. The first recording usually
  ends with a short lap, because auto-lap starts counting again in the next one.
- **Session summaries** are combined by rules: sums (distance, timer, calories, ascent,
  time in zones), maximums and minimums, timer-weighted averages (heart rate, cadence,
  power, temperature). Average speed, elapsed time, lap count and the bounding box are
  recomputed. A field without a rule is taken from the last recording, and the report
  names it if the recordings disagree.
- **The file header** (`file_id`, `sport`) comes from the first recording; everything else,
  including `device_info`, is copied byte for byte.

Files with several sessions, several FIT segments or compressed timestamps are not
supported yet: `fit-merge` refuses them with a clear error.

## Uploading to Strava

1. **Delete the original activity on Strava** if it's already there. Strava detects
   duplicates by start time and will reject the fixed file. After a merge, delete **all**
   the original recordings: the merged one starts at the first one's start time.
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

Merging works the same way, except it first builds one file out of several:

```
.fit ×N → reader → merge ─ splice → reader → fixup (aggregate) → writer → .merged.fit
```

- **splice**: copies the raw messages of all files one after another under a single header
  and CRC. All definition messages are copied, so every message decodes exactly as it did
  in its own file. Only the extra `file_id`, `sport`, sessions and `activity` are left out,
  along with the end-of-recording events of every file but the last.
- **fixup**: in the spliced file the values are still per file. They are rewritten in place
  with the same patches the cleaner uses: cumulative record fields (`distance` and others)
  are shifted, laps are renumbered, the end of recording becomes a pause, and the summaries
  are combined into the last file's session by the rules in **aggregate**.

## Development

```
pip install -e ".[dev]"
pytest
```

The tests build synthetic FIT files (`tests/fitbuilder.py`), so no real rides are needed in
the repository. To check against your own files:

```
set FIT_REPAIR_SAMPLE=C:\path\to\ride.fit
pytest tests/test_cli.py

set FIT_MERGE_SAMPLES=C:\path\to\part1.fit;C:\path\to\part2.fit
pytest tests/test_merge.py
```
