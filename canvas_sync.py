import os
import re
import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATA_SOURCE_ID = os.environ["NOTION_DATA_SOURCE_ID"]
GT_ICS_URL = os.environ["CANVAS_GT_ICS_URL"].strip()
FULTON_ICS_URL = os.environ["CANVAS_FULTON_ICS_URL"].strip()

# The Fulton feed covers multiple courses (school-wide events, Speech & Debate,
# FV AP Lit/Comp A). We only want assignments from FV AP Lit/Comp A, which is
# this course context ID (confirmed by inspecting the feed).
FULTON_COURSE_FILTER = "course_273100000000002740"

# Titles containing any of these (case-insensitive) are treated as assessments
# rather than regular assignments/classwork.
ASSESSMENT_KEYWORDS = ["exam", "test", "quiz", "assessment", "dba"]

NOTION_QUERY_URL = f"https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}

# --- ICS parsing --------------------------------------------------------
# Minimal parser: Canvas ICS feeds are simple enough that we don't need a
# full RFC 5545 library. Each VEVENT block is parsed for the handful of
# fields we care about.


def unfold_ics_lines(raw_text):
    # ICS "folds" long lines by breaking them with a newline + leading space.
    # Un-fold so each logical field is on one line.
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
            key_name = key.split(";")[0]  # drop parameters like ;VALUE=DATE
            current[key_name] = value
            if key_name == "DTSTART":
                current["DTSTART_ALL_DAY"] = "VALUE=DATE" in key
    return events


def ics_unescape(text):
    return (
        text.replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\n", " ")
        .replace("\\\\", "\\")
    )


def parse_due_date(dtstart, is_all_day):
    # Returns (date_string, is_datetime) in the format Notion expects.
    if is_all_day:
        # Format: YYYYMMDD
        return f"{dtstart[0:4]}-{dtstart[4:6]}-{dtstart[6:8]}", False
    # Format: YYYYMMDDTHHMMSSZ
    date_part = f"{dtstart[0:4]}-{dtstart[4:6]}-{dtstart[6:8]}"
    time_part = f"{dtstart[9:11]}:{dtstart[11:13]}:{dtstart[13:15]}"
    return f"{date_part}T{time_part}Z", True


# --- Fetch and filter events ---------------------------------------------


def extract_class_label(raw_summary):
    match = re.search(r"\[([^\]]*)\]\s*$", raw_summary)
    if not match:
        return "Unknown"
    parts = [p.strip() for p in match.group(1).split(",")]
    if len(parts) >= 3:
        return f"{parts[1]}: {parts[2]}"  # e.g. "MATH 1554: Linear Algebra"
    return match.group(1).strip()


def fetch_assignments(url, course_filter=None, class_label_override=None):
    response = requests.get(url)
    response.raise_for_status()
    events = parse_ics_events(response.text)

    assignments = []
    for event in events:
        uid = event.get("UID", "")
        if not uid.startswith("event-assignment"):
            continue  # skip office hours, holidays, school-wide events, etc.

        if course_filter and course_filter not in event.get("URL", ""):
            continue  # not the course we care about

        raw_summary = ics_unescape(event.get("SUMMARY", "(untitled)"))
        # Strip the trailing "[Course, Context, Tags]" bracket for a clean title
        title = re.sub(r"\s*\[[^\]]*\]\s*$", "", raw_summary).strip()
        class_label = class_label_override or extract_class_label(raw_summary)

        is_assessment = any(kw in title.lower() for kw in ASSESSMENT_KEYWORDS)

        dtstart = event.get("DTSTART", "")
        if not dtstart:
            continue

        if is_assessment:
            # Assessments always render as all-day, regardless of whether
            # Canvas gave us a specific time.
            due_date, is_datetime = parse_due_date(dtstart, is_all_day=True)
        else:
            due_date, is_datetime = parse_due_date(
                dtstart, is_all_day=event.get("DTSTART_ALL_DAY", False)
            )

        assignments.append(
            {
                "uid": uid,
                "title": title,
                "due_date": due_date,
                "is_datetime": is_datetime,
                "category": "Assessment" if is_assessment else "Schoolwork",
                "class_label": class_label,
            }
        )
    return assignments


# --- Sync into Notion ------------------------------------------------------


def find_notion_page_by_canvas_id(canvas_id):
    filter_body = {"filter": {"property": "Canvas Event ID", "rich_text": {"equals": canvas_id}}}
    response = requests.post(NOTION_QUERY_URL, headers=NOTION_HEADERS, json=filter_body)
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0] if results else None


def upsert_assignment(assignment):
    properties = {
        "Task name": {"title": [{"text": {"content": assignment["title"]}}]},
        "Category": {"select": {"name": assignment["category"]}},
        "Class": {"select": {"name": assignment["class_label"]}},
        "Due date": {
            "date": {
                "start": assignment["due_date"],
            }
        },
        "Canvas Event ID": {"rich_text": [{"text": {"content": assignment["uid"]}}]},
    }

    existing_page = find_notion_page_by_canvas_id(assignment["uid"])

    if existing_page:
        response = requests.patch(
            f"{NOTION_PAGES_URL}/{existing_page['id']}",
            headers=NOTION_HEADERS,
            json={"properties": properties},
        )
        response.raise_for_status()
        return "updated"
    else:
        response = requests.post(
            NOTION_PAGES_URL,
            headers=NOTION_HEADERS,
            json={"parent": {"data_source_id": DATA_SOURCE_ID}, "properties": properties},
        )
        response.raise_for_status()
        return "created"


def main():
    gt_assignments = fetch_assignments(GT_ICS_URL)
    fulton_assignments = fetch_assignments(
        FULTON_ICS_URL, course_filter=FULTON_COURSE_FILTER, class_label_override="AP Lit/Comp A"
    )
    all_assignments = gt_assignments + fulton_assignments

    print(f"Found {len(gt_assignments)} GT assignment(s), {len(fulton_assignments)} Fulton FV assignment(s).")

    created = updated = 0
    for assignment in all_assignments:
        result = upsert_assignment(assignment)
        if result == "created":
            created += 1
        else:
            updated += 1
        print(f"[{assignment['category']}] {result}: {assignment['title']} (due {assignment['due_date']})")

    print(f"Done. Created {created}, updated {updated}.")


if __name__ == "__main__":
    main()
