"""Paced Notion requests with bounded retries for explicitly rejected requests."""

import math
import time

import requests

_last_request = None


def notion_request(method, url, **kwargs):
    """Retry HTTP 429 only; ambiguous writes must not be repeated automatically."""
    global _last_request
    kwargs.setdefault('timeout', 30)
    for attempt in range(6):
        if _last_request is not None:
            time.sleep(max(0, 0.5 - (time.monotonic() - _last_request)))
        _last_request = time.monotonic()
        response = requests.request(method, url, **kwargs)
        if response.status_code != 429 or attempt == 5:
            response.raise_for_status()
            return response
        try:
            delay = float(response.headers['Retry-After'])
            if not math.isfinite(delay) or delay < 0:
                raise ValueError('Invalid Retry-After')
        except (KeyError, ValueError):
            delay = 2 ** attempt
        print(f'Notion rate limit: waiting {delay:g}s before retry {attempt + 1}/5.')
        response.close()
        time.sleep(delay)
