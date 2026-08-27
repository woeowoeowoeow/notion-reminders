#!/usr/bin/env python3
"""
Morning Brief Generator
Queries Notion for upcoming tasks and course feeds for announcements,
then sends a formatted email.
"""

import os
import sys
import json
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import feedparser
import pytz

# Configuration
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_TODO_DB_ID = os.getenv("NOTION_TODO_DB_ID")
NOTION_BRIEFS_DB_ID = os.getenv("NOTION_BRIEFS_DB_ID")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = "zoufvckyourself@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Course feeds
FEEDS = {
    "MATH 1554 (GT)": "https://gatech.instructure.com/feeds/announcements/enrollment_d1f7c82a-523b-4064-a593-b76a9e18a193.atom",
    "CS 1301 (GT)": "https://gatech.instructure.com/feeds/announcements/enrollment_408ac9bd-9e99-4ac7-9246-260ae101cd59.atom",
    "AP Lit/Comp A (Fulton)": "https://fultonvirtual.instructure.com/feeds/announcements/enrollment_0aQgQdVWSZi5UDG4Xf0dYL7AXEAGZvDFOSAib9zL.atom",
    "American Government (KSU)": "https://kennesaw.view.usg.edu/d2l/le/news/rss/4036203/course?token=a96u2y2mlcsv2m6uf0a25&ou=4036203",
    "Introduction to the Universe (KSU)": "https://kennesaw.view.usg.edu/d2l/le/news/rss/4034981/course?token=a96u2y2mlcsv2m6uf0a25&ou=4034981",
}

def query_notion(db_id, query_filter):
    """Query Notion database with filter."""
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    payload = {"filter": query_filter}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("results", [])
    except Exception as e:
        print(f"Error querying Notion: {e}")
        return []

def get_exams_and_tests():
    """Get exams/tests due in next 14 days."""
    two_weeks_from_now = (datetime.now() + timedelta(days=14)).date().isoformat()
    today = datetime.now().date().isoformat()

    filter_obj = {
        "and": [
            {"property": "Category", "select": {"equals": "Assessment"}},
            {"property": "Status", "select": {"does_not_equal": "Done"}},
            {"property": "Due Date", "date": {"on_or_after": today}},
            {"property": "Due Date", "date": {"on_or_before": two_weeks_from_now}},
        ]
    }

    results = query_notion(NOTION_TODO_DB_ID, filter_obj)
    items = []
    for result in results:
        try:
            title = result["properties"]["Name"]["title"][0]["text"]["content"]
            due_date = result["properties"]["Due Date"]["date"]["start"]
            items.append((title, due_date))
        except (KeyError, IndexError):
            continue

    items.sort(key=lambda x: x[1])
    return items

def get_this_week():
    """Get assignments due in next 7 days."""
    one_week_from_now = (datetime.now() + timedelta(days=7)).date().isoformat()
    today = datetime.now().date().isoformat()

    filter_obj = {
        "and": [
            {"property": "Status", "select": {"does_not_equal": "Done"}},
            {"property": "Due Date", "date": {"on_or_after": today}},
            {"property": "Due Date", "date": {"on_or_before": one_week_from_now}},
        ]
    }

    results = query_notion(NOTION_TODO_DB_ID, filter_obj)
    items = []
    for result in results:
        try:
            title = result["properties"]["Name"]["title"][0]["text"]["content"]
            due_date = result["properties"]["Due Date"]["date"]["start"]
            items.append((title, due_date))
        except (KeyError, IndexError):
            continue

    items.sort(key=lambda x: x[1])
    return items

def get_new_announcements():
    """Get announcements from feeds posted in last 24 hours."""
    cutoff = datetime.now(pytz.UTC) - timedelta(hours=24)
    announcements = {}

    for course_name, feed_url in FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            course_items = []

            for entry in feed.entries[:5]:  # Check last 5 entries
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime.fromtimestamp(
                        sum(entry.published_parsed[:6] + (0,) * 3) if entry.published_parsed else 0,
                        tz=pytz.UTC
                    )

                if pub_date and pub_date > cutoff:
                    title = entry.get('title', 'No title')
                    course_items.append(title)

            if course_items:
                announcements[course_name] = course_items
        except Exception as e:
            print(f"Error parsing {course_name} feed: {e}")
            continue

    return announcements

def format_brief(exams, assignments, announcements):
    """Format the brief as HTML email content."""
    today = datetime.now().strftime("%A, %B %d, %Y")

    # Format exams section
    exams_text = "Nothing to report."
    if exams:
        exams_text = "<br>".join([f"<strong>{title}</strong> — {due_date}" for title, due_date in exams])

    # Format assignments section
    assignments_text = "All clear."
    if assignments:
        assignments_text = "<br>".join([f"<strong>{title}</strong> — {due_date}" for title, due_date in assignments])

    # Format announcements section
    announcements_text = "No new announcements."
    if announcements:
        ann_lines = []
        for course, items in announcements.items():
            ann_lines.append(f"<strong>{course}:</strong> {', '.join(items)}")
        announcements_text = "<br>".join(ann_lines)

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #2c3e50;">Good Morning</h2>

        <h3 style="color: #34495e; margin-top: 20px;">Exams & Tests (Next 2 Weeks)</h3>
        <p>{exams_text}</p>

        <h3 style="color: #34495e; margin-top: 20px;">This Week</h3>
        <p>{assignments_text}</p>

        <h3 style="color: #34495e; margin-top: 20px;">Announcements</h3>
        <p>{announcements_text}</p>

        <p style="margin-top: 20px; font-size: 0.9em; color: #7f8c8d;">
            Generated on {today}
        </p>
    </body>
    </html>
    """

    # Plain text version
    text_content = f"""
Good Morning

EXAMS & TESTS (Next 2 Weeks)
{exams_text.replace('<br>', chr(10)).replace('<strong>', '').replace('</strong>', '')}

THIS WEEK
{assignments_text.replace('<br>', chr(10)).replace('<strong>', '').replace('</strong>', '')}

ANNOUNCEMENTS
{announcements_text.replace('<br>', chr(10)).replace('<strong>', '').replace('</strong>', '')}
"""

    return html_content, text_content.strip()

def send_email(subject, html_content, text_content):
    """Send formatted email."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

        print(f"✅ Email sent to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"❌ Error sending email: {e}")
        return False

def create_notion_entry(brief_text):
    """Create entry in Daily Briefs database."""
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    today = datetime.now().strftime("%Y-%m-%d")

    payload = {
        "parent": {"database_id": NOTION_BRIEFS_DB_ID},
        "properties": {
            "Name": {
                "title": [
                    {
                        "text": {
                            "content": today
                        }
                    }
                ]
            },
            "Content": {
                "rich_text": [
                    {
                        "text": {
                            "content": brief_text
                        }
                    }
                ]
            }
        }
    }

    try:
        response = requests.post(
            "https://api.notion.com/v1/pages",
            json=payload,
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        print("✅ Entry created in Daily Briefs database")
        return True
    except Exception as e:
        print(f"⚠️ Could not create Notion entry: {e}")
        return False

def main():
    """Generate and send morning brief."""
    print("📋 Generating morning brief...")

    # Check for required environment variables
    if not all([NOTION_API_KEY, NOTION_TODO_DB_ID, EMAIL_FROM, EMAIL_PASSWORD]):
        print("❌ Missing environment variables. Please set:")
        print("   - NOTION_API_KEY")
        print("   - NOTION_TODO_DB_ID")
        print("   - EMAIL_FROM")
        print("   - EMAIL_PASSWORD")
        sys.exit(1)

    # Gather data
    print("  Querying Notion...")
    exams = get_exams_and_tests()
    assignments = get_this_week()

    print("  Checking course feeds...")
    announcements = get_new_announcements()

    # Format brief
    html_content, text_content = format_brief(exams, assignments, announcements)

    # Send email
    today = datetime.now().strftime("%B %d, %Y")
    subject = f"Good Morning — {today}"
    send_email(subject, html_content, text_content)

    # Save to Notion (optional)
    if NOTION_BRIEFS_DB_ID:
        create_notion_entry(text_content)

    print("✅ Morning brief complete!")

if __name__ == "__main__":
    main()
