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


def normalize(text):
    return re.sub(r"\s+", " ", (text or "").strip())


# -------------------------
# RSS LOGIC (NEW CORE LOGIC)
# -------------------------
def get_status_and_message(feed):
    channel_desc = normalize(feed.feed.get("description", ""))
    lowered = channel_desc.lower()

    # STATUS from channel
    if "normal" in lowered:
        return "operational", channel_desc

    if "maintenance" in lowered:
        status = "under_maintenance"
    elif re.search(r"outage|down|unavailable", lowered):
        status = "major_outage"
    else:
        status = "degraded_performance"

    # MESSAGE from 2nd item (preferred)
    if len(feed.entries) > 1:
        entry = feed.entries[1]
    elif feed.entries:
        entry = feed.entries[0]
    else:
        return status, channel_desc

    message = normalize(
        entry.get("description")
        or entry.get("summary")
        or entry.get("title")
    )

    return status, message or channel_desc


# -------------------------
# STATUSPAGE API HELPERS
# -------------------------
def get_component(component_id):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/components/{component_id}"
    return requests.get(url, headers=HEADERS).json()


def update_component(component_id, status):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/components/{component_id}"
    requests.patch(url, headers=HEADERS, json={"component": {"status": status}})


def get_unresolved_incidents():
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/unresolved"
    return requests.get(url, headers=HEADERS).json()


def get_incident(incident_id):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/{incident_id}"
    return requests.get(url, headers=HEADERS).json()


def find_incident(name):
    prefix = f"[AUTO] {name}"
    for i in get_unresolved_incidents():
        if i["name"].startswith(prefix):
            return i
    return None


def latest_incident_body(incident_id):
    incident = get_incident(incident_id)
    updates = incident.get("incident_updates", [])
    if not updates:
        return ""
    return normalize(updates[0].get("body", ""))


# -------------------------
# INCIDENT HANDLING
# -------------------------
def create_incident(name, component_id, status, message):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents"

    payload = {
        "incident": {
            "name": f"[AUTO] {name} Issue",
            "status": "investigating",
            "body": message[:1000],
            "component_ids": [component_id],
            "components": {component_id: status},
        }
    }

    requests.post(url, headers=HEADERS, json=payload)
    print(f"{name}: created incident")


def update_incident(incident_id, name, component_id, status, message):
    new_body = normalize(message[:1000])
    current_body = latest_incident_body(incident_id)

    if new_body == current_body:
        print(f"{name}: message unchanged, skipping update")
        return

    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/{incident_id}"

    payload = {
        "incident": {
            "status": "investigating",
            "body": new_body,
            "components": {component_id: status},
        }
    }

    requests.patch(url, headers=HEADERS, json=payload)
    print(f"{name}: updated incident")


def resolve_incident(incident_id, name, component_id):
    url = f"https://api.statuspage.io/v1/pages/{STATUSPAGE_PAGE_ID}/incidents/{incident_id}"

    payload = {
        "incident": {
            "status": "resolved",
            "body": f"{name} is back to normal.",
            "components": {component_id: "operational"},
        }
    }

    requests.patch(url, headers=HEADERS, json=payload)
    print(f"{name}: resolved incident")


# -------------------------
# MAIN LOGIC
# -------------------------
def process_feed(feed_config):
    name = feed_config["name"]
    rss_url = feed_config["rss_url"]
    component_env = feed_config["component_env"]

    component_id = os.environ.get(component_env)
    if not component_id:
        raise RuntimeError(f"Missing env var: {component_env}")

    feed = feedparser.parse(rss_url)
    status, message = get_status_and_message(feed)

    print(f"{name}: status={status}")
    print(f"{name}: message={message}")

    current_status = get_component(component_id)["status"]

    if current_status != status:
        update_component(component_id, status)

    incident = find_incident(name)

    if status == "operational":
        if incident:
            resolve_incident(incident["id"], name, component_id)
        return

    if incident:
        update_incident(incident["id"], name, component_id, status, message)
    else:
        create_incident(name, component_id, status, message)


def main():
    with open("config/feeds.json", "r", encoding="utf-8-sig") as f:
        feeds = json.load(f)

    for feed in feeds:
        try:
            process_feed(feed)
        except Exception as e:
            print(f"ERROR: {feed.get('name')} → {e}")


if __name__ == "__main__":
    main()