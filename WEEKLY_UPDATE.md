# Updating the Site Each Week

## Session log

### 2026-09-13

Week 2 update surfaced a real inconsistency in the SOS/Quality-Loss grading,
found while explaining an Ohio State quality-loss bonus to the site owner:

- Ohio State's 1-point road loss to Texas earned a 3-pt Quality Loss bonus
  because Texas was ranked #22 in the reference used to grade the game --
  but `previous-standings.csv`'s own "Week 1" column (the number the site
  actually publishes) showed Texas at #32 for the same week. The #22 was a
  hidden intermediate value that never appeared anywhere on the site.
- Root cause: grading used `current-standings.csv` as the SOS reference
  (last run's fully-settled ranking, several weeks of retroactive recompute
  deep), while the *displayed* "Week N-1" column was computed fresh from
  that week's own games only -- two different, both-legitimate rankings that
  happened to diverge.
- Fixed by adding `scoring.score_season_two_pass()`: Pass 1 grades every
  game with the old reference (as before); Pass 2 recomputes the cumulative
  standings through `target_week - 1` from Pass 1's results and re-grades
  everything with *that*. The Pass 2 reference is now also pinned as the
  literal "Week N-1" column in `previous-standings.csv` (`write_previous_standings_csv`'s
  new `pinned_week`/`pinned_order` params) instead of being re-derived from
  the final scored data a second time, which used to drift it by a rank or
  two -- so the number used to grade a week's games is now always identical
  to the number the site publishes for the week before it.
- Net effect on this specific case: Texas's real Week 1 rank (#32) is above
  the Quality Loss cutoff (top 30), so Ohio State's bonus for that loss went
  away -- confirmed correct, not a regression.
- The multi-round-bundle path (`split_by_date_gap`, see 2026-09-07 below)
  wasn't touched -- it already chains each round off the standings right
  before it, sequentially, so it doesn't have this hidden-reference problem.
- Redid the Week 2 update from the pre-Week-2 snapshot with the fixed code
  rather than layering a correction on top of the already-pushed (buggy)
  Week 2 commit.

### 2026-09-07

Rerunning `--week 1` to pick up the games that were still pending on 2026-08-30
surfaced a real data bug: the 8 games that finished in time for that first run
turned out to all be a week-early "Week 0" slate (San Jose State/USC, Jax
State/North Dakota State, Sacramento State/Eastern Michigan, Hawai'i/Stanford,
New Mexico State/Florida State, Memphis/UNLV, North Carolina/TCU, NC
State/Virginia), not real Week 1 games -- ESPN's week=1 page for 2026 spans a
9-day window (confirmed via each game's own date header) and silently bundles
both rounds together since ESPN's week=0 page is an empty stub this year. That
made it look, after this run added the real Week 1 games, like 6 of those 12
teams had "played twice" in one week.

Fixed properly rather than one-off relabeled:

- `scrape.py` now reads each game's actual date off ESPN's page (it wasn't
  captured at all before).
- `update_week.py` clusters a scraped week's games by date and splits off any
  cluster more than 3 days older than the rest into `target_week - 1` (general
  gap-detection, not hardcoded to this one occurrence -- so it'll self-correct
  if this ESPN quirk recurs).
- Confirmed point totals/standings are byte-identical before and after the
  split -- this only fixes which week's column/file each game's points land
  in, never the scoring itself.
- Renamed the old preseason-seed "Week 0" column in `previous-standings.csv`
  to `Preseason`, since a real Week 0 (with actual games) now exists there too.

### 2026-08-30

Ran the real Week 1 update and found two scraper bugs in the process (both
fixed, see `scripts/scrape.py`):

- **Wrong ESPN page.** `espn_week_param()` used to unconditionally subtract 1
  from our week number, because 2025 had a real, separately-numbered ESPN
  "Week 0" exempt slate ahead of our week-1.csv. That's gone for 2026 — ESPN's
  `week=0` page is now a near-empty stub — so the offset is now year-aware:
  `-1` through 2025, `0` for 2026+. **If a future season's numbers look
  wrong, re-verify against the live scoreboard's page `<title>` before
  assuming last season's offset still applies** — don't just flip it blind.
- **Name/score misalignment on a mixed week.** The scraper used to read all
  team names and all scores as two separate flat lists and zip them together
  by position. A pregame card (not started yet) doesn't render a score
  element at all, so on a week with a mix of finished and not-yet-played
  games, the two lists could fall out of sync. Rewrote it to read each game's
  own name/score from within that game's own card
  (`section.Scoreboard`) — this also incidentally eliminated a "featured
  games rail" double-counting risk from the old code.
- Also added a **pending-games count**: `update_week.py`'s summary now warns
  when N games in the target week haven't finished yet, since `--week`
  reruns are the only way to pick up stragglers — the auto-detect logic
  moves on once a week has *any* rows, regardless of whether it's complete.
  Different weeks (`--week 1` vs `--week 2`) already pull from genuinely
  distinct ESPN pages with no overlap (verified by diffing full game grids,
  not just checking page titles), so this isn't a duplication risk — it's a
  "don't move on before the week is actually done" risk.

### 2026-08-25

Four commits, all same session:

- **Preseason standings** — `week0_seed_2026.csv` was still in old Baseline
  rank order even though 2026 scores under the Combined_All rule set;
  reordered it to match, and published that order as `current-standings.csv`
  so Current Standings shows real preseason content instead of sitting empty
  until Week 1 is scraped.
- **Mobile nav + SOS tiers** — mobile nav now collapses behind a 3-bar
  hamburger toggle below 768px instead of stacking every link; added a bold
  rule after ranks 30/60/100 on Current Standings marking where each SOS
  bonus tier cuts off.
- **Trimmed Current Standings intro** — cut four paragraphs of explanation
  down to one line linking to About the Standings; folded the moved content
  (ordering rule, tie-break rule) into the existing Point System section
  there.
- **Fixed 2026 conference realignment** — UTEP and Northern Illinois were
  still tagged to their old conferences (Conf USA / MAC) instead of Mountain
  West; Louisiana Tech was still Conf USA instead of Sun Belt; North Dakota
  State and Sacramento State (newly up from FCS) were missing from the
  roster entirely. Roster is now 138 teams, matching the real 2026 FBS
  count. Regenerated `current-standings.csv` to reflect the corrections.

## One-time setup (new machine only)

```
pip install -r scripts/requirements.txt
python -m playwright install chromium
```

## Every week

1. **Run the update script** from the repo root:
   ```
   python scripts/update_week.py
   ```
   It auto-detects the next week (based on which `week-N.csv` files already exist), scrapes ESPN, and rewrites `all-games.csv`, `current-standings.csv`, `total-points.csv`, `previous-standings.csv`, and `week-N.csv`.

   Useful flags:
   - `--week N` — process a specific week instead of auto-detecting (e.g. to redo a week if a late game finished after you already ran it)
   - `--year YYYY` — override the season year (defaults to the current one)
   - `--dry-run` — scrape and compute, but don't write any files, just print the summary

2. **Review the printed summary.**
   - Check the Top 10 and biggest movers for anything that looks obviously wrong.
   - Check the "Scraped names treated as FCS" list. If a real FBS team shows up there, its name didn't match the roster — see Troubleshooting below.
   - Check the "N game(s) not yet final" note. If it's non-zero (common if you run this before a week's whole slate has finished — e.g. Sunday/Monday games still to come), **re-run `--week N` again once those finish, before moving on to next week** — the script never automatically revisits an earlier week, so any games still pending when you move on get silently missed forever.

3. **(Optional) Preview locally before publishing:**
   ```
   npm start
   ```
   Open `http://localhost:3000`, click around, `Ctrl+C` to stop when done.

4. **Commit and push:**
   ```
   git add -A
   git commit -m "Week N update"
   git push
   ```

5. **Deploy** — your usual manual `gcloud run deploy` (unchanged).

## Kicking off a new season (Week 1)

Do the archive step and the first week's update together in **one** push+deploy, not two separate ones — `server.js` only loads the CSVs once at startup, so if you deploy with the old season archived away but no new data written yet, the live site shows completely blank pages until the next deploy.

1. Archive last season's files (preserves them with git history):
   ```
   git mv all-games.csv current-standings.csv previous-standings.csv total-points.csv week-*.csv archive/<old-year>/
   ```
2. Add `scripts/week0_seed_<new-year>.csv` (`Rank,Team`), seeded with the prior season's final rankings.
3. Update `WEEK0_SEED_PATH` in `scripts/update_week.py` to point at the new seed file.
4. Review `scripts/teams.json` for conference realignment / roster changes.
5. Run `python scripts/update_week.py --week 1`.
6. Commit, push, and deploy — all together.

## Troubleshooting

- **Scraper errors or times out:** confirm `python -m playwright install chromium` has been run on this machine. ESPN occasionally needs a retry (the script already retries once automatically).
- **A team's name shows up as "FCS" but it's actually FBS:** add an alias to the `"aliases"` section of `scripts/teams.json` mapping the scraped spelling to the canonical roster name, then rerun the week.
- **Conference realignment / new FBS members:** edit the `"conferences"` section of `scripts/teams.json` before the season starts.
- **Want to double-check the scoring engine or scraper are still working correctly:** `python scripts/regression_test_2025.py` (replays the whole archived 2025 season through the engine) or `python scripts/verify_scraper_2025.py` (scrapes a real past week and diffs it against archived data).
- **Worried a team's game might get double-counted or missed because a week's games span multiple days:** it won't be double-counted — each `--week N` pulls from ESPN's own distinct week=N page, and different weeks don't share games. It *can* be missed if you don't redo a week with pending games before moving to the next one — see the "not yet final" note in step 2 above.
