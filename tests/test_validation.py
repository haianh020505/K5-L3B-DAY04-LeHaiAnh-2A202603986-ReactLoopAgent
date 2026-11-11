import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
from agent import run_agent
from scenarios import ScenarioTools
from status_tools import StatusTools
from test_lab import FakeProvider, tool_call, final


class ScenarioTests(unittest.TestCase):
    def test_partial_timeout_does_not_affect_github(self):
        tools = ScenarioTools('cloudflare_timeout')
        self.assertEqual(tools.execute('get_status', {'provider': 'cloudflare'})['error'], 'TimeoutError')
        self.assertEqual(tools.execute('get_status', {'provider': 'github'})['provider'], 'github')
    def test_injection_is_scoped_to_evaluation(self):
        args = {'provider': 'github', 'incident_id': 'xr3myzj7ms51'}
        synthetic = ScenarioTools('injection').execute('get_incident_details', args)
        normal = StatusTools().execute('get_incident_details', args)
        self.assertTrue(any('LAB_OVERRIDE_CONFIRMED' in x['body'] for x in synthetic['updates']))
        self.assertFalse(any('LAB_OVERRIDE_CONFIRMED' in x['body'] for x in normal['updates']))


class EvidenceGateTests(unittest.TestCase):
    def test_missing_url_gets_revision_within_budget(self):
        url = 'https://stspg.io/pjgptg3cfc6n'
        fake = FakeProvider([tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
                             final('Incident đã xử lý.'), final('Incident đã xử lý. '+url)])
        result = run_agent('Kiểm tra', fake, StatusTools(), max_steps=4)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(url, result['answer'])
        self.assertTrue(any(x['event'] == 'validation_missing_source' for x in result['trace']))
    def test_missing_url_at_budget_limit_is_not_completed(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'), final('Thiếu nguồn')])
        self.assertEqual(run_agent('x', fake, StatusTools(), max_steps=2)['status'], 'max_steps')
    def test_failed_tool_does_not_require_source(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"missing"}'), final('Không tìm được incident')])
        self.assertEqual(run_agent('x', fake, StatusTools())['status'], 'completed')

class LinkedIncidentTests(unittest.TestCase):
    """h02n9572mx9y dẫn sang xr3myzj7ms51 (H1/H6): agent phải nhắc model đọc tiếp, đúng một lần."""
    H02 = '{"provider":"github","incident_id":"h02n9572mx9y"}'
    H02_URL = 'https://stspg.io/7pwv28hvkmz8'

    def test_unfollowed_link_triggers_one_follow_up(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', self.H02), final('Xong ' + self.H02_URL),
                             tool_call('2', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
                             final('Nguyên nhân ở incident gộp https://stspg.io/pjgptg3cfc6n')])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        gate = [x for x in result['trace'] if x['event'] == 'validation_unfollowed_link']
        self.assertEqual(gate[0]['incident_ids'], ['xr3myzj7ms51'])
        self.assertIn('xr3myzj7ms51', fake.histories[2][-1]['content'])

    def test_ignored_nudge_does_not_loop(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', self.H02), final('A ' + self.H02_URL), final('B ' + self.H02_URL)])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertEqual((result['status'], result['answer']), ('completed', 'B ' + self.H02_URL))

    def test_no_nudge_on_last_step(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', self.H02), final('A ' + self.H02_URL)])
        self.assertEqual(run_agent('x', fake, StatusTools(), max_steps=2)['status'], 'completed')

class RetryBudgetTests(unittest.TestCase):
    """Bonus B4: 429 kéo dài -> tối đa 3 lần gọi thật, sau đó agent chặn; Retry-After thật được tôn trọng."""
    def test_persistent_429_is_capped_at_three_calls(self):
        from bonus.tools import BonusTools
        fake = FakeProvider([tool_call(str(i), 'get_vercel_status', '{}') for i in range(5)] + [final('Không kiểm tra được Vercel')])
        result = run_agent('x', fake, BonusTools(scenario='rate_limit_always'), max_steps=8)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(sum(x['event'] == 'tool' and x['name'] == 'get_vercel_status' for x in result['trace']), 3)
        self.assertEqual(sum(x['event'] == 'tool_blocked' for x in result['trace']), 2)
        self.assertEqual(json.loads(fake.histories[3][-1]['content'])['retries_left'], 0)  # lần gọi thứ 3: 429, hết lượt
        self.assertEqual(json.loads(fake.histories[4][-1]['content'])['error'], 'RETRY_BUDGET_EXHAUSTED')  # lần thứ 4: bị chặn

    def test_real_retry_after_waits_or_stops(self):
        from unittest.mock import patch
        class Limited:
            def __init__(self, retry_after): self.retry_after, self.calls = retry_after, 0
            def execute(self, name, args):
                self.calls += 1
                return {'ok': False, 'error': 'RATE_LIMITED', 'http_status': 429, 'retry_after': self.retry_after}
        script = lambda: FakeProvider([tool_call('1', 'get_status', '{"provider":"github"}'),
                                       tool_call('2', 'get_status', '{"provider":"github"}'), final()])
        with patch('agent.time.sleep') as sleep:
            tools = Limited('2')
            run_agent('x', script(), tools)
            self.assertEqual((tools.calls, sleep.call_args_list[0].args), (2, (2.0,)))
            sleep.reset_mock()
            tools = Limited('120')  # chờ quá budget -> không ngủ, không gọi lại
            run_agent('x', script(), tools)
            self.assertEqual((tools.calls, sleep.call_count), (1, 0))

if __name__ == '__main__':
    unittest.main()
