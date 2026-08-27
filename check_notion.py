import os
import sys
import datetime
from collections import defaultdict
import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATA_SOURCE_ID = os.environ["NOTION_DATA_SOURCE_ID"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]  # e.g. a long random string, see README

# "in_progress" or "not_done" — passed as the first command-line argument
MODE = sys.argv[1] if len(sys.argv) > 1 else "in_progress"

NOTION_API_URL = f"https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}

if MODE == "in_progress":
    filter_body = {"filter": {"property": "Status", "status": {"equals": "In progress"}}}
    title = "Still In Progress"
elif MODE == "not_done":
    # Only tasks due within the next 7 days (computed fresh each run, so this
    # is a real rolling window, not a fixed date). Tasks with no due date set
    # are excluded, since they have nothing to compare against.
    seven_days_out = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    filter_body = {
        "filter": {
            "and": [
                {"property": "Status", "status": {"does_not_equal": "Done"}},
                {"property": "Due date", "date": {"on_or_before": seven_days_out}},
            ]
        }
    }
    title = "Incomplete Tasks Reminder"
else:
    raise ValueError(f"Unknown mode: {MODE}")

response = requests.post(NOTION_API_URL, headers=HEADERS, json=filter_body)

if response.status_code != 200:
    print(f"Notion API error {response.status_code}: {response.text}")
    response.raise_for_status()

results = response.json().get("results", [])

if not results:
    print("No matching tasks — nothing to notify.")
    sys.exit(0)

groups = defaultdict(list)
for page in results:
    props = page.get("properties", {})
    # NOTE: "Task name" must match your database's actual title property name exactly.
    title_prop = props.get("Task name", {}).get("title", [])
    name = title_prop[0]["plain_text"] if title_prop else "(untitled task)"

    class_prop = props.get("Class", {}).get("select")
    group_name = class_prop["name"] if class_prop else "Personal"

    groups[group_name].append(name)

# Personal always first, then every class alphabetically after it.
ordered_group_names = sorted(groups.keys(), key=lambda g: (g != "Personal", g))

sections = []
for group_name in ordered_group_names:
    bullet_list = "\n".join(f"* {name}" for name in groups[group_name])
    sections.append(f"{group_name}:\n\n{bullet_list}")

message = "\n\n".join(sections)
task_count = len(results)

ntfy_response = requests.post(
    f"https://ntfy.sh/{NTFY_TOPIC}",
    data=message.encode("utf-8"),
    headers={"Title": title, "Priority": "default", "Tags": "warning"},
)
ntfy_response.raise_for_status()
print(f"Notified about {task_count} task(s) across {len(groups)} group(s).")
