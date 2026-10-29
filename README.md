# EcoWatch — Environmental Monitoring Agent

EcoWatch is a small, local Python 3 prototype that checks a configurable list of public RSS/Atom feeds for new environmental-news items. It stores seen feed identifiers in `state.json`; subsequent runs report only items not seen by this installation. Each new item gets a keyword-based category, an extractive summary from the feed description, a low/medium/high potential-concern flag with matched terms, and practical suggested follow-ups. It writes `report.md` and a self-contained `index.html` dashboard.

## Run

Requires Python 3. No packages, paid APIs, model calls, recurring jobs, or server are required.

```sh
cd /home/node/ecowatch
python3 ecowatch.py
```

A per-feed network timeout can be set with `--timeout 20`. Use `--reset` to clear `state.json` before a run, making all items currently returned by feeds new again. Edit `sources.json` to add, remove, or change feeds. Feed URLs are checked independently: one failure does not stop the other sources. Generated files are written next to the script. `last_report.json` preserves the most recent run that found new items, so later empty runs update “Last checked” and the new-item count without erasing the last useful report. Open `index.html` directly for an offline dashboard; external source links need connectivity.

This prototype is new; it has not been independently reviewed for correctness or security.
Run offline tests with:

```sh
python3 -m unittest -v
```

## Current sources

- U.S. Geological Survey News — `https://feeds.feedburner.com/usgsnewsroom`
- NOAA News — `https://www.noaa.gov/news/rss.xml`
- ScienceDaily Earth & Climate — `https://www.sciencedaily.com/rss/earth_climate.xml`
- Nature Environmental Sciences — `https://www.nature.com/subjects/environmental-sciences.rss`

Feed availability and item counts can change. Configure other official or research feeds as appropriate.

## Limits

- Category, severity, and matched terms are keyword rules, not expert assessment. Items without relevant terms may be categorized as “environment.”
- Summary text is copied from the feed description (up to its first two sentences); EcoWatch does not independently verify it or infer missing context.
- A “potential concern” is only a screening prompt, not a verified environmental finding, emergency alert, or risk assessment. Suggested actions are generic and should be checked against the primary source and local context.
- Feed publishers can change formats, block requests, or go offline. Network failures are recorded per source; the monitor does not retry or guarantee completeness.
- Seen state is local to this directory; deleting it or changing identifiers causes previously seen items to appear new.
- Reports describe items returned by feeds on the run and do not provide continuous monitoring, notifications, or recurring execution.
