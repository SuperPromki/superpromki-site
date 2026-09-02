# SuperPromki — setup notes

This repo (`SuperPromki/superpromki-site`) holds the SuperPromki site plus
the scrapers that feed it live promo data.

## What's already done

- Repo created, files pushed (via the GitHub web editor).
- `GITHUB_RAW_BASE` in `promocje.html` already points at
  `https://raw.githubusercontent.com/SuperPromki/superpromki-site/main`,
  so once JSON files land in this repo, the live site picks them up
  automatically on next page load — no rebuild needed.
- `.github/workflows/scrape-promotions.yml` runs all scrapers
  (`scrape_lidl.py`, `scrape_biedronka.py`, `scrape_kaufland.py`,
  `scrape_biedronka_food.py`, `scrape_zabka.py`, `scrape_netto.py`,
  `scrape_stokrotka.py`, `scrape_auchan.py`) daily at 06:00 UTC and commits
  whatever changed.

## Next steps

1. **Enable GitHub Pages** so the site is actually reachable at a URL:
   repo **Settings → Pages → Deploy from a branch → main → / (root)** →
   Save. It'll be live at
   `https://superpromki.github.io/superpromki-site/promocje.html` within a
   minute or two.
2. **Run the scrape workflow once manually** to populate the JSON files
   instead of waiting for the 06:00 UTC schedule: repo **Actions** tab →
   "Scrape store promotions" → **Run workflow**.
3. **Check the run's logs** if `kaufland_oferta.json` or
   `biedronka_home.json` don't show up — `scrape_biedronka.py` and
   `scrape_kaufland.py` are marked `continue-on-error: true` in the
   workflow precisely because their selectors are best-effort (see the
   comment at the top of each script) and may need a small fix once you
   see what actually broke against the live markup.

## Working on this locally later

If you ever want to edit these files from your own machine instead of the
GitHub web editor:

```bash
git clone https://github.com/SuperPromki/superpromki-site.git
cd superpromki-site
# make changes, then:
git add .
git commit -m "describe your change"
git push
```

(If `git` asks you to log in, use a GitHub personal access token as the
password — GitHub stopped accepting plain passwords for git operations.)
