# Morning Brief Setup Guide

## Step 1: Install Dependencies

Open Terminal and run:

```bash
pip3 install requests feedparser pytz
```

## Step 2: Get Your Notion Credentials

1. **Get your Notion API Key:**
   - Go to https://www.notion.so/my-integrations
   - Click "Create new integration"
   - Name it "Morning Brief"
   - Copy the "Internal Integration Token" (starts with `secret_...`)

2. **Get your Todo List Database ID:**
   - Open your Notion Todo List
   - Click the "..." menu → "Copy link to database"
   - The URL will look like: `https://www.notion.so/[WORKSPACE]/[DATABASE_ID]?...`
   - Extract the long ID before the `?` — that's your database ID

3. **Share the database with your integration:**
   - In Notion, open your Todo List database
   - Click "Share" → "Add a guest"
   - Search for "Morning Brief" and add it

## Step 3: Set Up GitHub Secrets

If using GitHub Actions, add these secrets to your repository:
- `NOTION_API_KEY`
- `NOTION_TODO_DB_ID`
- `EMAIL_FROM`
- `EMAIL_PASSWORD`

## Step 4: Test the Script Locally

Run it manually to test:
```bash
NOTION_API_KEY="..." NOTION_TODO_DB_ID="..." EMAIL_FROM="..." EMAIL_PASSWORD="..." python3 morning_brief.py
```

## Notes

- For Gmail, use an App Password, not your regular password
- GitHub Actions runs on UTC, so cron `0 10 * * *` = 6 AM EDT
- Check your email spam folder if you don't see the brief
