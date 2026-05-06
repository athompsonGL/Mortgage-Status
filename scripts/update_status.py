import json
import os
import re
import sys

import feedparser
import requests


STATUSPAGE_API_KEY = os.environ["STATUSPAGE_API_KEY"]
STATUSPAGE_PAGE_ID = os.environ["STATUSPAGE_PAGE_ID"]

HEADERS = {
    "Authorization": f"OAuth {STATUSPAGE_API_KEY}",
    "Content-Type": "application/json",
}

BAD_KEYWORDS = (
    r"maintenance|scheduled maintenance|special maintenance|outage|unavailable|"
    r"down|latency|degraded|performance|incident|issue|disruption|interruption|"
    r"alert|advisory|systems affected"
)


def clean_join(values):
    seen = []
    for value in values:
        value = (value or "").strip()
        if value and value not in seen:
            seen.append(value)
    return " ".join(seen).strip()


def normalize_text(text):
    return re.sub(r"\s+", " ", (text or "").strip())


def pick_relevant_rss_entry(feed):
    channel_description = normalize_text(feed.feed.get("description", ""))

    if channel_description:
        return channel_description

    if feed.entries:
        entry = feed.entries[0]
        return clean_join([
            entry.get("title", ""),
            entry.get("summary", ""),
            entry.get("description", ""),
        ])

    return "Normal"


def map_component_status(text):
    lowered = text.lower()

    if re.search(r"\bnormal\b|operational", lowered):
        return "operational"

    if re.search(r"maintenance|scheduled maintenance|special maintenance", lowered):
        return "under_maintenance"

    if re.search(r"outage|unavailable|down|service interruption|interruption", lowered):
        return "major_outage"

    if re.search(r"latency|degraded|performance|incident|issue|disruption|alert|advisory|systems affected", lowered):
        return "degraded_performance"

    return "operational"


def map_incident_status(component_status):
    if component_status == "operational":
        return "resolved"

    if component_status == "under_maintenance":
        return "monitoring"

    return "investigating"


def get_component(component_id):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/components/{component_id}"
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def update_component(component_id, new_status):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/components/{component_id}"
    response = requests.patch(
        url,
        headers=HEADERS,
        json={"component": {"status": new_status}},
        timeout=15,
    )
    response.raise_for_status()


def get_unresolved_incidents():
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/unresolved"
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def get_incident(incident_id):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/{incident_id}"
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def find_existing_incident(component_name):
    expected_prefix = f"[AUTO] {component_name}"

    for incident in get_unresolved_incidents():
        if incident.get("name", "").startswith(expected_prefix):
            return incident

    return None


def latest_incident_body(incident_id):
    incident = get_incident(incident_id)
    updates = incident.get("incident_updates", [])

    if not updates:
        return ""

    return normalize_text(updates[0].get("body", ""))


def create_incident(component_name, component_id, component_status, message):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents"

    payload = {
        "incident": {
            "name": f"[AUTO] {component_name} - Issue Detected",
            "status": map_incident_status(component_status),
            "body": message[:1000],
            "components": {
                component_id: component_status
            },
            "component_ids": [
                component_id
            ]
        }
    }

    response = requests.post(url, headers=HEADERS, json=payload, timeout=15)
    response.raise_for_status()
    print(f"{component_name}: created incident")


def update_incident(incident_id, component_name, component_id, component_status, message):
    current_body = latest_incident_body(incident_id)
    new_body = normalize_text(message[:1000])

    if current_body == new_body:
        print(f"{component_name}: incident message unchanged, skipping update")
        return

    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/{incident_id}"

    payload = {
        "incident": {
            "status": map_incident_status(component_status),
            "body": new_body,
            "components": {
                component_id: component_status
            }
        }
    }

    response = requests.patch(url, headers=HEADERS, json=payload, timeout=15)
    response.raise_for_status()
    print(f"{component_name}: updated incident message")


def resolve_incident(incident_id, component_name, component_id):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/{incident_id}"

    payload = {
        "incident": {
            "status": "resolved",
            "body": f"{component_name} has returned to normal.",
            "components": {
                component_id: "operational"
            }
        }
    }

    response = requests.patch(url, headers=HEADERS, json=payload, timeout=15)
    response.raise_for_status()
    print(f"{component_name}: resolved incident")


def process_feed(feed_config):
    name = feed_config["name"]
    rss_url = feed_config["rss_url"]
    component_env = feed_config["component_env"]

    component_id = os.environ.get(component_env)

    if not component_id:
        raise RuntimeError(f"Missing environment variable: {component_env}")

    feed = feedparser.parse(rss_url)

    if not feed.entries and not feed.feed.get("description"):
        raise RuntimeError(f"No RSS entries or channel description found for {name}")

    latest_text = normalize_text(pick_relevant_rss_entry(feed))

    print(f"{name}: selected RSS text: {latest_text}")

    new_status = map_component_status(latest_text)
    current_status = get_component(component_id)["status"]

    print(f"{name}: current={current_status}, new={new_status}")

    if current_status != new_status:
        update_component(component_id, new_status)
        print(f"{name}: component updated to {new_status}")
    else:
        print(f"{name}: component unchanged")

    existing_incident = find_existing_incident(name)

    if new_status == "operational":
        if existing_incident:
            resolve_incident(existing_incident["id"], name, component_id)
        else:
            print(f"{name}: no open incident to resolve")
        return

    if existing_incident:
        update_incident(
            existing_incident["id"],
            name,
            component_id,
            new_status,
            latest_text,
        )
    else:
        create_incident(
            name,
            component_id,
            new_status,
            latest_text,
        )


def main():
    with open("config/feeds.json", "r", encoding="utf-8-sig") as f:
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