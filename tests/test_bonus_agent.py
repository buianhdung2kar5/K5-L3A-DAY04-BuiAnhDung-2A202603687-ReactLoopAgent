"""Test offline cho cải tiến bonus: retry 429 có giới hạn và nhắc đọc chi tiết incident. Không gọi LLM API."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
from agent import run_agent, MAX_ATTEMPTS
from bonus.tools import BonusTools
from grading import mechanical_checks
from status_tools import StatusTools

CASES = {c['id']: c for c in json.loads((ROOT / 'bonus' / 'cases.json').read_text(encoding='utf-8'))}


def calls(*items):
    """Một lượt model gọi một hoặc nhiều tool: items = (call_id, name, args)."""
    cs, tcs = [], []
    for call_id, name, args in items:
        raw = json.dumps(args)
        cs.append({'id': call_id, 'name': name, 'arguments': raw})
        tcs.append({'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': raw}})
    return {'message': {'role': 'assistant', 'content': None, 'tool_calls': tcs},
            'calls': cs, 'finish': 'tool_calls', 'usage': {}}


def final(answer):
    return {'message': {'role': 'assistant', 'content': answer}, 'calls': [], 'finish': 'stop', 'usage': {}}


class Scripted:
    def __init__(self, responses):
        self.responses, self.histories = iter(responses), []

    def complete(self, messages, schemas):
        self.histories.append(list(messages))
        return next(self.responses)


def events(result, name):
    return [e for e in result['trace'] if e['event'] == name]


def tool_count(result, name):
    return sum(1 for e in result['trace'] if e['event'] == 'tool' and e['name'] == name)


class RetryTests(unittest.TestCase):
    def test_b10_like_no_retry_is_nudged_until_success(self):
        # Baseline B10: model thấy 429 rồi trả lời luôn -> vi phạm min_calls. Sau sửa: được nhắc gọi lại.
        p = Scripted([calls(('a', 'get_github_status', {}), ('b', 'get_vercel_status', {})),
                      final('Vercel bị rate limit.'),
                      calls(('c', 'get_vercel_status', {})),
                      final('Vercel operational sau lần thử thứ 2.')])
        result = run_agent(CASES['B10']['question'], p, BonusTools(scenario='rate_limit_once'), max_steps=16)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(events(result, 'validation_retry_pending')), 1)
        self.assertEqual(tool_count(result, 'get_vercel_status'), 2)
        first = json.loads(p.histories[1][-1]['content'])
        self.assertEqual(first['error'], 'RATE_LIMITED')  # lỗi vẫn trả nguyên cho model
        self.assertIn('1/3', first['agent_note'])
        violations = mechanical_checks(CASES['B10'], result)['violations']
        self.assertFalse([v for v in violations if 'get_vercel_status' in v])

    def test_b4_like_persistent_429_stops_at_three_calls(self):
        p = Scripted([calls(('a', 'get_github_status', {}), ('b', 'get_vercel_status', {})),
                      final('Không biết Vercel.'),
                      calls(('c', 'get_vercel_status', {})),
                      final('Vẫn không biết Vercel.'),
                      calls(('d', 'get_vercel_status', {})),
                      final('Đã thử 3 lần, không xác định được trạng thái Vercel.')])
        result = run_agent(CASES['B4']['question'], p, BonusTools(scenario='rate_limit_always'), max_steps=16)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tool_count(result, 'get_vercel_status'), MAX_ATTEMPTS)
        self.assertEqual(len(events(result, 'validation_retry_pending')), 2)
        last = json.loads(p.histories[5][-1]['content'])
        self.assertIn('hết lượt thử', last['agent_note'])
        self.assertEqual(mechanical_checks(CASES['B4'], result)['violations'], [])

    def test_fourth_call_is_blocked_and_not_counted_as_tool(self):
        p = Scripted([calls(('a', 'get_vercel_status', {}))] + [calls((f'r{i}', 'get_vercel_status', {})) for i in range(4)]
                     + [final('Không xác định được Vercel sau 3 lần.')])
        result = run_agent('Kiểm tra Vercel', p, BonusTools(scenario='rate_limit_always'), max_steps=16)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tool_count(result, 'get_vercel_status'), MAX_ATTEMPTS)
        self.assertEqual(len(events(result, 'retry_blocked')), 2)
        blocked = json.loads(p.histories[4][-1]['content'])
        self.assertEqual(blocked['error'], 'RETRY_BUDGET_EXHAUSTED')
        self.assertFalse(mechanical_checks({'max_calls': {'get_vercel_status': 3}}, result)['violations'])

    def test_no_retry_nudge_after_success(self):
        p = Scripted([calls(('a', 'get_vercel_status', {})), calls(('b', 'get_vercel_status', {})), final('OK')])
        result = run_agent('Kiểm tra Vercel', p, BonusTools(scenario='rate_limit_once'), max_steps=16)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(events(result, 'validation_retry_pending'), [])

    def test_retry_after_is_respected_but_bounded(self):
        class Live429:
            schemas, system_prompt = [], 'x'

            def execute(self, name, args):
                return {'ok': False, 'error': 'RATE_LIMITED', 'http_status': 429, 'retry_after': '0.01', 'retriable': True}
        p = Scripted([calls(('a', 'get_npm_status', {})), final('Không xác định npm.')] + [
            calls(('b', 'get_npm_status', {})), final('x'), calls(('c', 'get_npm_status', {})), final('Đã thử 3 lần.')])
        result = run_agent('npm?', p, Live429(), max_steps=16)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tool_count(result, 'get_npm_status'), MAX_ATTEMPTS)


class DetailNudgeTests(unittest.TestCase):
    def test_b6_like_status_only_is_nudged_to_read_details(self):
        t = BonusTools(scenario='noisy')
        v = t.execute('get_vercel_status', {'component': 'Builds'})['incidents'][0]['id']
        url = BonusTools().execute('get_incident_details', {'provider': 'vercel', 'incident_id': v})['url']
        p = Scripted([calls(('a', 'get_vercel_status', {'component': 'Builds'}), ('b', 'get_npm_status', {'component': 'package publishing'})),
                      final('Vercel và npm đều resolved.'),
                      calls(('c', 'get_incident_details', {'provider': 'vercel', 'incident_id': v})),
                      final('Đã đọc chi tiết. ' + url)])
        result = run_agent(CASES['B6']['question'], p, t, max_steps=16)
        self.assertEqual(result['status'], 'completed')
        ev = events(result, 'validation_unread_incident')
        self.assertEqual(len(ev), 1)
        self.assertIn(f'vercel {v}', ev[0]['incidents'])
        self.assertFalse([x for x in mechanical_checks(CASES['B6'], result)['violations'] if 'get_incident_details' in x])

    def test_no_detail_nudge_for_overview_question(self):
        # B1/B10: hỏi tổng quan status, không cần đọc chi tiết incident.
        p = Scripted([calls(('a', 'get_npm_status', {})), final('npm operational')])
        result = run_agent(CASES['B7']['question'], p, BonusTools(), max_steps=16)
        self.assertEqual(events(result, 'validation_unread_incident'), [])

    def test_detail_nudge_disabled_for_core_tools(self):
        # Bài chính không bị ảnh hưởng: StatusTools không có thuộc tính schemas.
        p = Scripted([calls(('a', 'get_status', {'provider': 'github', 'component': 'Actions'})),
                      final('Incident mới nhất của Actions đã resolved.')])
        result = run_agent('Phạm vi incident mới nhất của GitHub Actions?', p, StatusTools(), max_steps=8)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(events(result, 'validation_unread_incident'), [])


if __name__ == '__main__':
    unittest.main()
