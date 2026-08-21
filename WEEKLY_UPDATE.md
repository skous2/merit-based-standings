# Updating the Site Each Week

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
