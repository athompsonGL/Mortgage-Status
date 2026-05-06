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


def map_component_status(text: str) -> str:
    text = text.lower()

    if "normal" in text or "operational" in text:
        return "operational"

    if re.search(r"maintenance|scheduled maintenance", text):
        return "under_maintenance"

    if re.search(r"outage|unavailable|down|service interruption", text):
        return "major_outage"

    if re.search(r"latency|degraded|performance|incident|issue|disruption", text):
        return "degraded_performance"

    return "degraded_performance"


def map_incident_status(component_status: str) -> str:
    if component_status == "operational":
        return "resolved"

    if component_status == "under_maintenance":
        return "monitoring"

    return "investigating"


def get_component(component_id: str) -> dict:
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/components/{component_id}"
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def update_component(component_id: str, new_status: str):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/components/{component_id}"

    response = requests.patch(
        url,
        headers=HEADERS,
        json={"component": {"status": new_status}},
        timeout=15,
    )
    response.raise_for_status()


def get_unresolved_incidents() -> list:
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/unresolved"
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def find_existing_incident(component_name: str) -> dict | None:
    incidents = get_unresolved_incidents()

    expected_prefix = f"[AUTO] {component_name}"

    for incident in incidents:
        if incident.get("name", "").startswith(expected_prefix):
            return incident

    return None


def create_incident(component_name: str, component_id: str, component_status: str, message: str):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents"

    incident_name = f"[AUTO] {component_name} - Issue Detected"

    payload = {
        "incident": {
            "name": incident_name,
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


def update_incident(incident_id: str, component_name: str, component_id: str, component_status: str, message: str):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/{incident_id}"

    payload = {
        "incident": {
            "status": map_incident_status(component_status),
            "body": message[:1000],
            "components": {
                component_id: component_status
            }
        }
    }

    response = requests.patch(url, headers=HEADERS, json=payload, timeout=15)
    response.raise_for_status()

    print(f"{component_name}: updated incident")


def resolve_incident(incident_id: str, component_name: str, component_id: str):
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

    title = latest.get("title", "").strip()
    summary = latest.get("summary", "").strip()
    description = latest.get("description", "").strip()

    latest_text = " ".join([title, summary, description]).strip()

    print(f"{name}: latest RSS text: {latest_text}")

    new_status = map_component_status(latest_text)
    component = get_component(component_id)
    current_status = component["status"]

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

    incident_message = latest_text or f"{name} is reporting {new_status}"

    if existing_incident:
        update_incident(
            existing_incident["id"],
            name,
            component_id,
            new_status,
            incident_message,
        )
    else:
        create_incident(
            name,
            component_id,
            new_status,
            incident_message,
        )


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