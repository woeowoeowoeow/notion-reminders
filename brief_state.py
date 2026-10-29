"""Durable delivery receipts. Missing or unreadable state fails closed."""

import base64
import json
from datetime import date
from pathlib import Path

import requests


def receipt_date(data):
    value = data['last_sent_date']
    if value is not None:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError('Invalid delivery receipt date')
    return value


class GitHubDeliveryState:
    def __init__(self, repository, token, api_url='https://api.github.com'):
        self.url = f'{api_url}/repos/{repository}/contents/morning-brief.json'
        self.headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        }
        self.sha = None

    def load(self):
        response = requests.get(self.url, headers=self.headers,
                                params={'ref': 'automation-state'}, timeout=30)
        response.raise_for_status()
        blob = response.json()
        value = receipt_date(json.loads(base64.b64decode(blob['content'])))
        self.sha = blob['sha']
        if not self.sha:
            raise ValueError('Missing receipt SHA')
        return value

    def mark_sent(self, day):
        if self.sha is None:
            raise ValueError('Load delivery receipt before updating it')
        receipt_date({'last_sent_date': day})
        content = json.dumps({'last_sent_date': day}) + '\n'
        response = requests.put(self.url, headers=self.headers, timeout=30, json={
            'message': f'Record morning brief delivery for {day}',
            'branch': 'automation-state',
            'sha': self.sha,
            'content': base64.b64encode(content.encode()).decode(),
        })
        # A conflict or failure must be visible: email may already have been sent.
        response.raise_for_status()


class FileDeliveryState:
    """For local use only; callers must serialize runs, as Actions does."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        return receipt_date(json.loads(self.path.read_text()))

    def mark_sent(self, day):
        receipt_date({'last_sent_date': day})
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'last_sent_date': day}) + '\n')
        temporary.replace(self.path)
