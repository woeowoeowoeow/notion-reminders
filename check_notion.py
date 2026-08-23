import os
import sys
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
    filter_body = {"filter": {"property": "Status", "status": {"does_not_equal": "Done"}}}
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

task_names = []
for page in results:
    props = page.get("properties", {})
    # NOTE: "Task name" must match your database's actual title property name exactly.
    title_prop = props.get("Task name", {}).get("title", [])
    name = title_prop[0]["plain_text"] if title_prop else "(untitled task)"
    task_names.append(name)

message = "\n".join(f"- {name}" for name in task_names)

ntfy_response = requests.post(
    f"https://ntfy.sh/{NTFY_TOPIC}",
    data=message.encode("utf-8"),
    headers={"Title": title, "Priority": "default", "Tags": "warning"},
)
ntfy_response.raise_for_status()
print(f"Notified about {len(task_names)} task(s): {task_names}")
