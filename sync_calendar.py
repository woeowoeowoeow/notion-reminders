import os
import json
import time
import requests
from google.oauth2 import service_account
import google.auth.transport.requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATA_SOURCE_ID = os.environ["NOTION_DATA_SOURCE_ID"]
GOOGLE_SERVICE_ACCOUNT_KEY = os.environ["GOOGLE_SERVICE_ACCOUNT_KEY"]  # full JSON key, as a string
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "justinzoupersonal@gmail.com")

SYNC_WINDOW_DAYS = 14  # how far ahead to pull events

NOTION_QUERY_URL = f"https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}

# --- Google auth -----------------------------------------------------------
# Uses a service account (not OAuth), so there's no refresh token to expire.
# The calendar must be shared with the service account's email as a viewer.

service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT_KEY)
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/calendar.readonly"],
)
credentials.refresh(google.auth.transport.requests.Request())
GOOGLE_ACCESS_TOKEN = credentials.token

# --- Fetch events from Google Calendar --------------------------------------

time_min = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
time_max = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + SYNC_WINDOW_DAYS * 86400))

calendar_url = (
    f"https://www.googleapis.com/calendar/v3/calendars/"
    f"{requests.utils.quote(GOOGLE_CALENDAR_ID, safe='')}/events"
)
calendar_response = requests.get(
    calendar_url,
    headers={"Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}"},
    params={
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 250,
    },
)

if calendar_response.status_code != 200:
    print(f"Google Calendar API error {calendar_response.status_code}: {calendar_response.text}")
    calendar_response.raise_for_status()

events = calendar_response.json().get("items", [])
print(f"Found {len(events)} Google Calendar event(s) in the next {SYNC_WINDOW_DAYS} days.")

# --- Sync each event into the Todo List database ----------------------------
# Events are upserted using the Google Event ID stored on each Notion page,
# so re-runs update existing pages instead of creating duplicates.

created = 0
updated = 0
archived = 0

for event in events:
    event_id = event["id"]
    is_cancelled = event.get("status") == "cancelled"

    find_body = {"filter": {"property": "Google Event ID", "rich_text": {"equals": event_id}}}
    find_response = requests.post(NOTION_QUERY_URL, headers=NOTION_HEADERS, json=find_body)
    find_response.raise_for_status()
    matches = find_response.json().get("results", [])
    existing_page = matches[0] if matches else None

    if is_cancelled:
        if existing_page:
            archive_response = requests.patch(
                f"{NOTION_PAGES_URL}/{existing_page['id']}",
                headers=NOTION_HEADERS,
                json={"archived": True},
            )
            archive_response.raise_for_status()
            archived += 1
            print(f"Archived cancelled event: {event.get('summary', event_id)}")
        continue

    title = event.get("summary", "(untitled event)")
    start = event["start"].get("dateTime", event["start"].get("date"))
    end = event["end"].get("dateTime", event["end"].get("date"))

    properties = {
        "Task name": {"title": [{"text": {"content": title}}]},
        "Category": {"select": {"name": "Event"}},
        "Status": {"status": {"name": "Event"}},
        "Due date": {
            "date": {
                "start": start,
                "end": end if end != start else None,
            }
        },
        "Google Event ID": {"rich_text": [{"text": {"content": event_id}}]},
    }

    if existing_page:
        update_response = requests.patch(
            f"{NOTION_PAGES_URL}/{existing_page['id']}",
            headers=NOTION_HEADERS,
            json={"properties": properties},
        )
        update_response.raise_for_status()
        updated += 1
    else:
        create_response = requests.post(
            NOTION_PAGES_URL,
            headers=NOTION_HEADERS,
            json={"parent": {"data_source_id": DATA_SOURCE_ID}, "properties": properties},
        )
        create_response.raise_for_status()
        created += 1

print(f"Done. Created {created}, updated {updated}, archived {archived}.")
