import base64
import importlib
import json
import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import requests


class HTTPFixture(unittest.TestCase):
    def setUp(self):
        self.responses = []
        self.calls = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond()

            do_POST = do_PATCH = do_PUT = do_GET

            def respond(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                fixture.calls.append((self.command, self.path, json.loads(body) if body else None))
                status, headers, payload = fixture.responses.pop(0)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)


class NotionHTTPTests(HTTPFixture):
    def setUp(self):
        super().setUp()
        self.client = importlib.import_module('notion_http')
        self.client._last_request = None
        self.clock = 0.0

        def sleep(seconds):
            self.clock += seconds

        self.sleep = patch.object(self.client.time, 'sleep', side_effect=sleep).start()
        patch.object(self.client.time, 'monotonic', side_effect=lambda: self.clock).start()
        self.addCleanup(patch.stopall)

    def test_rejected_create_waits_for_retry_after_then_succeeds(self):
        self.responses = [(429, {'Retry-After': '7'}, {}), (200, {}, {'id': 'created'})]
        response = self.client.notion_request('POST', self.url, json={'properties': {}})
        self.assertEqual(response.json()['id'], 'created')
        self.assertGreaterEqual(self.clock, 7)
        self.assertEqual(len(self.calls), 2)

    def test_requests_are_paced(self):
        self.responses = [(200, {}, {}), (200, {}, {})]
        self.client.notion_request('POST', self.url)
        self.client.notion_request('PATCH', self.url)
        self.assertGreaterEqual(self.clock, 0.5)

    def test_persistent_rate_limit_fails_after_bounded_retries(self):
        self.responses = [(429, {'Retry-After': '1'}, {})] * 6
        with self.assertRaises(requests.HTTPError):
            self.client.notion_request('POST', self.url)
        self.assertEqual(len(self.calls), 6)

    def test_invalid_retry_after_uses_backoff(self):
        self.responses = [(429, {'Retry-After': 'bad'}, {}), (200, {}, {})]
        self.client.notion_request('POST', self.url)
        self.assertGreaterEqual(self.clock, 1)

    def test_permanent_and_ambiguous_errors_are_not_retried(self):
        for code in (400, 401, 403, 500, 503):
            with self.subTest(code=code):
                self.responses = [(code, {}, {})]
                with self.assertRaises(requests.HTTPError):
                    self.client.notion_request('POST', self.url)
                self.assertEqual(self.responses, [])

    def test_transport_failure_does_not_repeat_create(self):
        with patch.object(self.client.requests, 'request', side_effect=requests.Timeout) as send:
            with self.assertRaises(requests.Timeout):
                self.client.notion_request('POST', self.url)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.kwargs['timeout'], 30)


class DeliveryStateTests(HTTPFixture):
    def store(self):
        module = importlib.import_module('brief_state')
        return module.GitHubDeliveryState('owner/repo', 'test-token', api_url=self.url)

    def receipt(self, last_sent):
        return {'sha': 'original-sha', 'content': base64.b64encode(
            json.dumps({'last_sent_date': last_sent}).encode()).decode()}

    def test_receipt_survives_a_new_process_and_uses_sha_on_update(self):
        self.responses = [(200, {}, self.receipt(None)), (200, {}, {}),
                          (200, {}, self.receipt('2026-10-29'))]
        store = self.store()
        self.assertIsNone(store.load())
        store.mark_sent('2026-10-29')
        self.assertEqual(self.store().load(), '2026-10-29')
        payload = self.calls[1][2]
        self.assertEqual(payload['sha'], 'original-sha')
        self.assertEqual(payload['branch'], 'automation-state')
        self.assertEqual(json.loads(base64.b64decode(payload['content'])),
                         {'last_sent_date': '2026-10-29'})

    def test_unreadable_or_invalid_state_fails_closed(self):
        for status, body in ((404, {}), (403, {}), (500, {}),
                             (200, self.receipt('nonsense')), (200, {'sha': 'x'})):
            with self.subTest(status=status, body=body):
                self.responses = [(status, {}, body)]
                with self.assertRaises((requests.HTTPError, ValueError, KeyError)):
                    self.store().load()

    def test_conflicting_receipt_write_fails(self):
        self.responses = [(200, {}, self.receipt(None)), (409, {}, {})]
        store = self.store()
        store.load()
        with self.assertRaises(requests.HTTPError):
            store.mark_sent('2026-10-29')


class MorningBriefTests(unittest.TestCase):
    def setUp(self):
        self.brief = importlib.import_module('morning_brief')

    def test_late_start_sends_once_per_eastern_day(self):
        from brief_state import FileDeliveryState
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipt.json'
            path.write_text('{"last_sent_date": null}')
            state = FileDeliveryState(path)
            with patch.multiple(self.brief, NOTION_TOKEN='token', NOTION_DATA_SOURCE_ID='id',
                                EMAIL_FROM='from@example.com', EMAIL_PASSWORD='password'), \
                 patch.object(self.brief, 'get_exams_and_tests', return_value=[]), \
                 patch.object(self.brief, 'get_this_week', return_value=[]), \
                 patch.object(self.brief, 'get_new_announcements', return_value={}), \
                 patch.object(self.brief, 'send_email') as send:
                self.brief.main(now=datetime(2026, 10, 29, 17, tzinfo=timezone.utc), state=state)
                self.brief.main(now=datetime(2026, 10, 29, 18, tzinfo=timezone.utc),
                                state=FileDeliveryState(path))
                self.assertEqual(send.call_count, 1)
                self.assertEqual(json.loads(path.read_text())['last_sent_date'], '2026-10-29')

    def test_dst_and_midnight_eligibility(self):
        for now, last, expected in (
            (datetime(2026, 10, 29, 9, tzinfo=timezone.utc), None, False),
            (datetime(2026, 10, 29, 10, tzinfo=timezone.utc), None, True),
            (datetime(2026, 11, 3, 10, tzinfo=timezone.utc), None, False),
            (datetime(2026, 11, 3, 11, tzinfo=timezone.utc), None, True),
            (datetime(2026, 10, 30, 3, tzinfo=timezone.utc), '2026-10-29', False),
            (datetime(2026, 10, 30, 4, tzinfo=timezone.utc), '2026-10-29', False),
            (datetime(2026, 10, 30, 10, tzinfo=timezone.utc), '2026-10-29', True),
        ):
            with self.subTest(now=now, last=last):
                self.assertEqual(self.brief.brief_is_due(now, last), expected)

    def test_smtp_refusal_is_a_failure(self):
        import smtplib
        with patch.object(self.brief.smtplib, 'SMTP') as smtp:
            smtp.return_value.__enter__.return_value.sendmail.return_value = {'recipient': (550, b'no')}
            with self.assertRaises(smtplib.SMTPRecipientsRefused):
                self.brief.send_email('Subject', '<p>Body</p>', 'Body')

    def test_failed_send_does_not_record_delivery(self):
        import tempfile
        from pathlib import Path
        from brief_state import FileDeliveryState
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipt.json'
            path.write_text('{"last_sent_date": null}')
            with patch.multiple(self.brief, NOTION_TOKEN='token', NOTION_DATA_SOURCE_ID='id',
                                EMAIL_FROM='from@example.com', EMAIL_PASSWORD='password'), \
                 patch.object(self.brief, 'get_exams_and_tests', return_value=[]), \
                 patch.object(self.brief, 'get_this_week', return_value=[]), \
                 patch.object(self.brief, 'get_new_announcements', return_value={}), \
                 patch.object(self.brief, 'send_email', side_effect=RuntimeError('SMTP failure')):
                with self.assertRaisesRegex(RuntimeError, 'SMTP failure'):
                    self.brief.main(now=datetime(2026, 10, 29, 17, tzinfo=timezone.utc),
                                    state=FileDeliveryState(path))
                self.assertIsNone(json.loads(path.read_text())['last_sent_date'])

    def test_receipt_read_failure_prevents_send(self):
        from brief_state import FileDeliveryState
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder, patch.object(self.brief, 'send_email') as send:
            state = FileDeliveryState(Path(folder) / 'missing.json')
            with self.assertRaises(FileNotFoundError):
                self.brief.main(now=datetime(2026, 10, 29, 17, tzinfo=timezone.utc), state=state)
            self.assertEqual(send.call_count, 0)

    def test_receipt_write_failure_fails_after_send(self):
        from brief_state import FileDeliveryState
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipt.json'
            path.write_text('{"last_sent_date": null}')
            state = FileDeliveryState(path)
            with patch.multiple(self.brief, NOTION_TOKEN='token', NOTION_DATA_SOURCE_ID='id',
                                EMAIL_FROM='from@example.com', EMAIL_PASSWORD='password'), \
                 patch.object(self.brief, 'get_exams_and_tests', return_value=[]), \
                 patch.object(self.brief, 'get_this_week', return_value=[]), \
                 patch.object(self.brief, 'get_new_announcements', return_value={}), \
                 patch.object(self.brief, 'send_email') as send, \
                 patch.object(state, 'mark_sent', side_effect=OSError('receipt write failed')):
                with self.assertRaisesRegex(OSError, 'receipt write failed'):
                    self.brief.main(now=datetime(2026, 10, 29, 17, tzinfo=timezone.utc), state=state)
                self.assertEqual(send.call_count, 1)

    def test_recent_rss_timestamp_is_recognized(self):
        published = format_datetime(datetime.now(timezone.utc) - timedelta(hours=1))
        feed = f'<rss version="2.0"><channel><title>Course</title><item><title>New announcement</title><pubDate>{published}</pubDate></item></channel></rss>'
        with patch.object(self.brief, 'FEEDS', {'Course': feed.encode()}):
            self.assertEqual(self.brief.get_new_announcements(), {'Course': ['New announcement']})


class WorkflowTests(unittest.TestCase):
    def test_production_jobs_share_a_queue_without_canceling_pending_runs(self):
        from pathlib import Path
        import yaml
        groups = set()
        for path in Path('.github/workflows').glob('*.yml'):
            data = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            if 'schedule' not in data['on']:
                continue
            groups.add(data['concurrency']['group'])
            self.assertEqual(data['concurrency']['queue'], 'max')
            self.assertEqual(data['concurrency']['cancel-in-progress'], 'false')
        self.assertEqual(len(groups), 1)

    def test_state_commits_do_not_trigger_ci(self):
        from pathlib import Path
        import yaml
        data = yaml.load(Path('.github/workflows/tests.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(data['on']['push']['branches'], ['main'])

    def test_in_progress_workflow_sends_reminders(self):
        from pathlib import Path
        import yaml
        data = yaml.load(Path('.github/workflows/check-in-progress.yml').read_text(), Loader=yaml.BaseLoader)
        steps = data['jobs']['check']['steps']
        step = next(step for step in steps if step.get('run', '').startswith('python '))
        self.assertEqual(step['run'].split(), ['python', 'check_notion.py', 'in_progress'])
        self.assertIn('NTFY_TOPIC', step['env'])


if __name__ == '__main__':
    unittest.main()
