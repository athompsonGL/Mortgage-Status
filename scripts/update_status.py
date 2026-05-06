import json
import os
import re
import sys

import feedparser
import requests


STATUSPAGE_API_KEY = os.environ["STATUSPAGE_API_KEY"]
STATUSPAGE_PAGE_ID = os.environ["STATUSPAGE_PAGE_ID"]


def map_status(text: str) -> str:
    text = text.lower()

    if "normal" in text or "operational" in text:
        return "operational"

    if re.search(r"maintenance|scheduled maintenance", text):
        return "under_maintenance"

    if re.search(r"outage|unavailable|down|service interruption", text):
        return "major_outage"

    if re.search(r"latency|degraded|performance|incident|issue|disruption", text):
        return "degraded_performance"

    # Safer fallback: do not mark operational if unsure
    return "degraded_performance"


def update_component(component_name: str, component_id: str, new_status: str):
    url = (
        f"https://api.statuspage.io/v1/pages/"
        f"{STATUSPAGE_PAGE_ID}/components/{component_id}"
    )

    headers = {
        "Authorization": f"OAuth {STATUSPAGE_API_KEY}",
        "Content-Type": "application/json",
    }

    current_resp = requests.get(url, headers=headers, timeout=15)
    current_resp.raise_for_status()

    current_status = current_resp.json()["status"]

    print(f"{component_name}: current={current_status}, new={new_status}")

    if current_status == new_status:
        print(f"{component_name}: no change")
        return

    patch_resp = requests.patch(
        url,
        headers=headers,
        json={"component": {"status": new_status}},
        timeout=15,
    )
    patch_resp.raise_for_status()

    print(f"{component_name}: updated to {new_status}")


def process_feed(feed_config: dict):
    name = feed_config["name"]
    rss_url = feed_config["rss_url"]
    component_env = feed_config["component_env"]

    component_id = os.environ.get(component_env)

    if not component_id:
        raise RuntimeError(f"Missing environment variable: {component_env}")

    feed = feedparser.parse(rss_url)

    if not feed.entries:
        raise RuntimeError(f"No RSS entries found for {name}")

    latest = feed.entries[0]

    latest_text = " ".join(
        [
            latest.get("title", ""),
            latest.get("summary", ""),
            latest.get("description", ""),
        ]
    )

    print(f"{name}: latest RSS text: {latest_text}")

    new_status = map_status(latest_text)

    update_component(name, component_id, new_status)


def main():
    with open("config/feeds.json", "r", encoding="utf-8") as f:
        feeds = json.load(f)

    failures = 0

    for feed_config in feeds:
        try:
            process_feed(feed_config)
        except Exception as e:
            failures += 1
            print(f"ERROR processing {feed_config.get('name', 'unknown')}: {e}")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()