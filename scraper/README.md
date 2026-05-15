# Apify -> Facebook Ads Library -> Qualified Leads -> Google Sheet

A reusable lead pipeline. Runs on demand from the GitHub Actions tab (and can be flipped to a cron in one line).

## What it does

1. Hits the Facebook / Meta Ads Library through an Apify actor for every `(keyword × country)` pair in `config.yaml`.
2. Groups all the returned ads by advertiser page and counts active ads per page.
3. Keeps only pages whose active-ad count sits inside the qualification window (default **10–50** — real spend, still scaling).
4. Dedupes against pages already in your sheet (by Page ID).
5. Appends the survivors to the configured Google Sheet tab.

## One-time setup

### 1. Apify token

1. Go to <https://console.apify.com/account/integrations> and copy your API token. (**Rotate the one you pasted in chat — it's compromised.**)
2. In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `APIFY_TOKEN`
   - Value: the new token

### 2. Google service account + sheet

1. <https://console.cloud.google.com/> → create (or pick) a project.
2. **APIs & Services → Library** → enable **Google Sheets API** and **Google Drive API**.
3. **APIs & Services → Credentials → Create credentials → Service account**. Name it `lead-scraper`, click through, skip the optional steps.
4. Open the new service account → **Keys → Add key → JSON**. A JSON file downloads.
5. Open your destination Google Sheet (create one if needed) and **Share** it with the service account's `client_email` (found in the JSON) as an **Editor**.
6. Copy the sheet ID from the URL (`docs.google.com/spreadsheets/d/<THIS_PART>/edit`) and paste it into `scraper/config.yaml` → `google_sheet_id`.
7. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `GOOGLE_SERVICE_ACCOUNT_JSON`
   - Value: paste the entire contents of the JSON file

### 3. Commit the sheet ID

```bash
git add scraper/config.yaml
git commit -m "configure google_sheet_id"
git push
```

## Running it

### Manual

GitHub repo → **Actions** tab → **Scrape Qualified Leads** → **Run workflow**. All four inputs are optional — leave them blank to use `config.yaml`, or override any one of them for a one-off run (e.g. set `countries=US` to test a single market).

### Scheduled

In `.github/workflows/scrape-leads.yml`, uncomment the `schedule:` block. The default cron there is weekly Monday 06:00 UTC; change to taste.

## Tuning

Everything live-tunable lives in `scraper/config.yaml`:

- `keywords` — phrases queried against Ads Library. Add ones that match your ideal customer's marketing language.
- `countries` — ISO country codes. More countries = more queries = more Apify cost.
- `count_per_query` — max ads pulled per `(keyword × country)`. Raise for wider coverage.
- `qualification.min_active_ads` / `max_active_ads` — the qualification window. Tighten to filter harder.
- `apify_actor_id` — swap if you prefer a different Ads Library actor.

## Cost notes

Each run costs Apify compute units proportional to `keywords × countries × count_per_query`. With the default config that's ~16 × 6 × 50 = 4,800 ad records — usually well under a dollar on Apify's free / starter tier. Watch your Apify usage page for the first couple of runs and tune downward if needed.

## Output schema

| Column | Notes |
|---|---|
| Page ID | Facebook Page ID. Primary dedupe key. |
| Page Name | Advertiser name. |
| FB Page URL | Link to the Facebook page profile. |
| Active Ads | Count inside the qualification window. |
| Landing Pages | Newline-separated CTA destinations extracted from the ads. The outreach surface. |
| Categories | FB page categories. |
| Sample Ad Text | First ad's body copy, truncated to 500 chars. |
| Date Added | Run date. |
