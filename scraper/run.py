"""Scrape Facebook Ads Library via Apify, qualify advertisers, append to Sheet.

Pipeline: build search URLs from config -> run Apify actor -> aggregate ads by
page -> keep pages with min_active_ads <= count <= max_active_ads -> dedupe
against the existing sheet by Page ID -> append new rows.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import gspread
import yaml
from apify_client import ApifyClient
from google.oauth2.service_account import Credentials

CONFIG_PATH = Path(__file__).with_name("config.yaml")

SHEET_HEADERS = [
    "Page ID",
    "Page Name",
    "FB Page URL",
    "Active Ads",
    "Landing Pages",
    "Categories",
    "Sample Ad Text",
    "Date Added",
]


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


def env_override(key: str, default: Any) -> Any:
    val = os.environ.get(key, "").strip()
    return val if val else default


def split_csv(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def build_search_urls(keywords: list[str], countries: list[str]) -> list[dict[str, str]]:
    urls = []
    for country in countries:
        for kw in keywords:
            params = {
                "active_status": "active",
                "ad_type": "all",
                "country": country,
                "q": kw,
                "sort_data[direction]": "desc",
                "sort_data[mode]": "relevancy_monthly_grouped",
            }
            urls.append({"url": f"https://www.facebook.com/ads/library/?{urlencode(params)}"})
    return urls


def run_actor(client: ApifyClient, actor_id: str, search_urls: list[dict[str, str]], count_per_query: int) -> list[dict]:
    run_input = {
        "urls": search_urls,
        "count": count_per_query,
        "scrapePageAds.activeStatus": "active",
        "period": "",
    }
    print(f"Starting Apify actor {actor_id} with {len(search_urls)} search URLs...")
    run = client.actor(actor_id).call(run_input=run_input)
    if not run or not run.get("defaultDatasetId"):
        raise RuntimeError(f"Actor run failed or returned no dataset: {run}")
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


def _first(*values: Any) -> Any:
    for v in values:
        if v:
            return v
    return None


def _extract_cta(ad: dict) -> str | None:
    snapshot = ad.get("snapshot") or {}
    cards = snapshot.get("cards") or []
    candidates = [
        snapshot.get("link_url"),
        snapshot.get("cta_url"),
        ad.get("link_url"),
        ad.get("cta_url"),
        ad.get("ad_creative_link_url"),
    ]
    for c in cards:
        if isinstance(c, dict):
            candidates.append(c.get("link_url"))
    for c in candidates:
        if c and isinstance(c, str) and c.startswith("http"):
            return c
    return None


def _extract_body_text(ad: dict) -> str:
    snapshot = ad.get("snapshot") or {}
    body = snapshot.get("body")
    if isinstance(body, dict):
        text = body.get("text") or body.get("markup", {}).get("__html") if isinstance(body.get("markup"), dict) else body.get("text")
        if text:
            return str(text)
    return str(_first(
        ad.get("ad_creative_body"),
        ad.get("ad_creative_bodies"),
        snapshot.get("body_text"),
        snapshot.get("caption"),
        "",
    ))


def aggregate_by_page(items: Iterable[dict]) -> dict[str, dict]:
    pages: dict[str, dict] = defaultdict(lambda: {
        "ads": [],
        "page_name": None,
        "page_url": None,
        "page_id": None,
        "categories": [],
        "cta_urls": set(),
    })
    for ad in items:
        snapshot = ad.get("snapshot") or {}
        page_id = _first(
            ad.get("page_id"),
            ad.get("pageId"),
            snapshot.get("page_id"),
        )
        if not page_id:
            continue
        pid = str(page_id)
        p = pages[pid]
        p["ads"].append(ad)
        p["page_id"] = pid
        p["page_name"] = p["page_name"] or _first(
            ad.get("page_name"),
            ad.get("pageName"),
            snapshot.get("page_name"),
        )
        p["page_url"] = p["page_url"] or _first(
            ad.get("page_profile_uri"),
            snapshot.get("page_profile_uri"),
            f"https://www.facebook.com/{pid}",
        )
        cats = _first(ad.get("page_categories"), snapshot.get("page_categories"), ad.get("categories"))
        if cats and not p["categories"]:
            p["categories"] = cats if isinstance(cats, list) else [cats]
        cta = _extract_cta(ad)
        if cta:
            p["cta_urls"].add(cta)
    return pages


def qualify(pages: dict[str, dict], min_ads: int, max_ads: int) -> list[dict]:
    qualified = []
    for p in pages.values():
        n = len(p["ads"])
        if min_ads <= n <= max_ads:
            p["active_ad_count"] = n
            qualified.append(p)
    qualified.sort(key=lambda x: x["active_ad_count"], reverse=True)
    return qualified


def open_worksheet(sheet_id: str, tab_name: str):
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=1000, cols=len(SHEET_HEADERS))
    return ws


def ensure_headers(ws) -> None:
    existing = ws.row_values(1)
    if not existing:
        ws.append_row(SHEET_HEADERS, value_input_option="RAW")


def existing_page_ids(ws) -> set[str]:
    col = ws.col_values(1)
    return set(col[1:]) if len(col) > 1 else set()


def append_qualified(ws, qualified: list[dict]) -> int:
    existing = existing_page_ids(ws)
    today = time.strftime("%Y-%m-%d")
    rows = []
    for p in qualified:
        if p["page_id"] in existing:
            continue
        sample_text = _extract_body_text(p["ads"][0]) if p["ads"] else ""
        rows.append([
            p["page_id"],
            p["page_name"] or "",
            p["page_url"] or "",
            p["active_ad_count"],
            "\n".join(sorted(p["cta_urls"])),
            ", ".join(str(c) for c in p["categories"]) if p["categories"] else "",
            str(sample_text)[:500],
            today,
        ])
    if rows:
        ws.append_rows(rows, value_input_option="RAW")
    return len(rows)


def main() -> int:
    cfg = load_config()

    keywords = split_csv(env_override("KEYWORDS_OVERRIDE", cfg["keywords"]))
    countries = split_csv(env_override("COUNTRIES_OVERRIDE", cfg["countries"]))
    min_ads = int(env_override("MIN_ADS_OVERRIDE", cfg["qualification"]["min_active_ads"]))
    max_ads = int(env_override("MAX_ADS_OVERRIDE", cfg["qualification"]["max_active_ads"]))
    count_per_query = int(cfg.get("count_per_query", 50))
    actor_id = cfg["apify_actor_id"]
    sheet_id = cfg["google_sheet_id"]
    tab_name = cfg.get("google_sheet_tab", "Qualified Leads")

    if sheet_id == "REPLACE_WITH_YOUR_SHEET_ID":
        print("ERROR: edit scraper/config.yaml and set google_sheet_id.", file=sys.stderr)
        return 2
    if not keywords or not countries:
        print("ERROR: keywords and countries must both be non-empty.", file=sys.stderr)
        return 2

    print(f"Queries: {len(keywords)} keywords x {len(countries)} countries = {len(keywords) * len(countries)}")
    print(f"Qualification window: {min_ads}-{max_ads} active ads per page")

    client = ApifyClient(os.environ["APIFY_TOKEN"])
    search_urls = build_search_urls(keywords, countries)
    items = run_actor(client, actor_id, search_urls, count_per_query)
    print(f"Scraped {len(items)} ad records")

    pages = aggregate_by_page(items)
    print(f"Aggregated into {len(pages)} unique advertiser pages")

    qualified = qualify(pages, min_ads, max_ads)
    print(f"{len(qualified)} pages passed the qualification gate")

    ws = open_worksheet(sheet_id, tab_name)
    ensure_headers(ws)
    added = append_qualified(ws, qualified)
    print(f"Appended {added} new qualified leads to '{tab_name}' "
          f"(skipped {len(qualified) - added} already in the sheet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
