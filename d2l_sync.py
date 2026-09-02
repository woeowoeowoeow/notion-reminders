import os
import re
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATA_SOURCE_ID = os.environ["NOTION_DATA_SOURCE_ID"]
D2L_ICS_URL = os.environ["D2L_KSU_ICS_URL"].strip()

# Titles containing any of these (case-insensitive) are treated as assessments
# rather than regular assignments/classwork. Same rule as the Canvas sync.
ASSESSMENT_KEYWORDS = ["exam", "test", "quiz", "assessment", "dba"]

# D2L/Brightspace emits DTSTART as a floating/TZID-qualified local time (no
# trailing Z) or, less commonly, as literal UTC (trailing Z). When no TZID
# is present on a local (non-Z) timestamp, assume US/Eastern -- that's the
# timezone KSU's Brightspace instance actually runs on.
DEFAULT_TZID = "America/New_York"

NOTION_QUERY_URL = f"https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}

# --- ICS parsing (same minimal approach as canvas_sync.py) -----------------


REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}


def unfold_ics_lines(raw_text):
    lines = raw_text.replace("\r\n", "\n").split("\n")
    unfolded = []
    for line in lines:
        if line.startswith(" ") and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def parse_ics_events(raw_text):
    events = []
    current = None
    for line in unfold_ics_lines(raw_text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, value = line.partition(":")
            key_name = key.split(";")[0]
            current[key_name] = value
            if key_name == "DTSTART":
                # Capture the TZID parameter, if present, e.g.
                # "DTSTART;TZID=America/New_York" -> "America/New_York"
                tzid_match = re.search(r"TZID=([^;:]+)", key)
                current["DTSTART_TZID"] = tzid_match.group(1) if tzid_match else None
    return events


def ics_unescape(text):
    return (
        text.replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\n", " ")
        .replace("\\\\", "\\")
    )


def parse_due_date(dtstart, tzid=None):
    """Returns an ISO-8601 UTC datetime string for Notion.

    dtstart is either:
      - YYYYMMDDTHHMMSSZ  (literal UTC per RFC 5545 -- use as-is)
      - YYYYMMDDTHHMMSS   (local time -- floating or TZID-qualified;
                            interpret in `tzid`, or DEFAULT_TZID if none
                            was given, and convert to real UTC)
    """
    date_part = f"{dtstart[0:4]}-{dtstart[4:6]}-{dtstart[6:8]}"
    time_part = f"{dtstart[9:11]}:{dtstart[11:13]}:{dtstart[13:15]}"

    if dtstart.endswith("Z"):
        return f"{date_part}T{time_part}Z"

    naive = datetime(
        int(dtstart[0:4]), int(dtstart[4:6]), int(dtstart[6:8]),
        int(dtstart[9:11]), int(dtstart[11:13]), int(dtstart[13:15]),
    )
    local = naive.replace(tzinfo=ZoneInfo(tzid or DEFAULT_TZID))
    utc = local.astimezone(ZoneInfo("UTC"))
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_course_name(location):
    # "American Government Section W06 Fall Semester 2026 CO" -> "American Government"
    return re.sub(r"\s+Section\s.*$", "", location).strip()


# --- Resolve the Available / Due / Availability Ends trio ------------------
# D2L emits up to 3 separate events per assignment. We only want one entry
# per assignment: prefer "- Due", fall back to "- Availability Ends" for
# item types (e.g. Discussion Assignments) that never get a "- Due" event.
# "- Available" (and its bare/no-suffix variant with no due semantics) is
# always skipped.

SUFFIXES = [
    (" - Due", "due"),
    (" - Availability Ends", "availability_ends"),
    (" - Available", "available"),
]


def classify_event(raw_summary):
    for suffix, kind in SUFFIXES:
        if raw_summary.endswith(suffix):
            return raw_summary[: -len(suffix)], kind
    return raw_summary, "none"  # no recognized suffix — treat as a real item


def resolve_assignments(events):
    parsed = []
    for event in events:
        if "SUMMARY" not in event or "DTSTART" not in event or "LOCATION" not in event:
            continue
        raw_summary = ics_unescape(event["SUMMARY"])
        title, kind = classify_event(raw_summary)
        course = clean_course_name(ics_unescape(event["LOCATION"]))
        parsed.append(
            {
                "uid": event.get("UID", ""),
                "title": title.strip(),
                "course": course,
                "kind": kind,
                "dtstart": event["DTSTART"],
                "dtstart_tzid": event.get("DTSTART_TZID"),
            }
        )

    due_keys = {(e["title"], e["course"]) for e in parsed if e["kind"] == "due"}

    assignments = []
    for e in parsed:
        key = (e["title"], e["course"])
        if e["kind"] == "available":
            continue  # content unlock notice, never the due date
        if e["kind"] == "availability_ends" and key in due_keys:
            continue  # a real "- Due" event exists for this item, skip the fallback
        # "due", "none", or "availability_ends" without a "- Due" sibling
        assignments.append(e)
    return assignments


# --- Sync into Notion ------------------------------------------------------


def find_notion_page_by_d2l_id(d2l_id):
    filter_body = {"filter": {"property": "D2L Event ID", "rich_text": {"equals": d2l_id}}}
    response = requests.post(NOTION_QUERY_URL, headers=NOTION_HEADERS, json=filter_body)
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0] if results else None


def upsert_assignment(assignment):
    title = assignment["title"]
    is_assessment = any(kw in assignment["title"].lower() for kw in ASSESSMENT_KEYWORDS)
    due_date = parse_due_date(assignment["dtstart"], tzid=assignment.get("dtstart_tzid"))

    properties = {
        "Task name": {"title": [{"text": {"content": title}}]},
        "Category": {"select": {"name": "Assessment" if is_assessment else "Schoolwork"}},
        "Class": {"select": {"name": assignment["course"]}},
        "Due date": {"date": {"start": due_date}},
        "D2L Event ID": {"rich_text": [{"text": {"content": assignment["uid"]}}]},
    }

    existing_page = find_notion_page_by_d2l_id(assignment["uid"])

    if existing_page:
        response = requests.patch(
            f"{NOTION_PAGES_URL}/{existing_page['id']}",
            headers=NOTION_HEADERS,
            json={"properties": properties},
        )
        response.raise_for_status()
        return "updated", title, is_assessment
    else:
        response = requests.post(
            NOTION_PAGES_URL,
            headers=NOTION_HEADERS,
            json={"parent": {"data_source_id": DATA_SOURCE_ID}, "properties": properties},
        )
        response.raise_for_status()
        return "created", title, is_assessment


def main():
    response = requests.get(D2L_ICS_URL, headers=REQUEST_HEADERS)
    print(f"Fetched feed: status {response.status_code}, {len(response.text)} chars, content-type {response.headers.get('content-type')}")
    if not response.text.strip().startswith("BEGIN:VCALENDAR"):
        print("Response doesn't look like ICS content. Full body below:")
        print(response.text)
    response.raise_for_status()
    events = parse_ics_events(response.text)
    print(f"Parsed {len(events)} raw calendar event(s) from the feed.")
    assignments = resolve_assignments(events)

    print(f"Found {len(assignments)} assignment(s) across all KSU courses.")

    created = updated = 0
    for assignment in assignments:
        result, title, is_assessment = upsert_assignment(assignment)
        if result == "created":
            created += 1
        else:
            updated += 1
        category = "Assessment" if is_assessment else "Schoolwork"
        print(f"[{category}] {result}: {title}")

    print(f"Done. Created {created}, updated {updated}.")


if __name__ == "__main__":
    main()
