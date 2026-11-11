"""Test offline cho cải tiến H6/H1: đi theo liên kết sang incident được dẫn tới. Dùng FakeProvider."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import run_agent
from grading import mechanical_checks
from status_tools import StatusTools
from test_lab import FakeProvider, tool_call, final

TOOLS = StatusTools()
# h02n9572mx9y có update dẫn tới xr3myzj7ms51 (dữ liệu thật trong snapshot).
URL_H02N = TOOLS.get_incident_details('github', 'h02n9572mx9y')['url']
URL_XR3M = TOOLS.get_incident_details('github', 'xr3myzj7ms51')['url']


def details(incident_id, call_id):
    return tool_call(call_id, 'get_incident_details',
                     json.dumps({'provider': 'github', 'incident_id': incident_id}))


def events(result, name):
    return [e for e in result['trace'] if e['event'] == name]


class FollowLinkTests(unittest.TestCase):
    def test_unfollowed_link_is_nudged_and_read_in_a_later_step(self):
        fake = FakeProvider([details('h02n9572mx9y', 'a'), final('Chỉ mô tả ảnh hưởng. ' + URL_H02N),
                             details('xr3myzj7ms51', 'b'), final('Đã đọc incident gộp. ' + URL_H02N + ' ' + URL_XR3M)])
        result = run_agent('Điều tra incident h02n9572mx9y', fake, StatusTools(), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        ev = events(result, 'validation_unfollowed_link')
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]['incidents'], ['xr3myzj7ms51'])
        nudge = fake.histories[2][-1]  # lời nhắc gửi ở lần gọi model thứ 3
        self.assertEqual(nudge['role'], 'user')
        self.assertIn('xr3myzj7ms51', nudge['content'])
        self.assertIn('giữ nguyên', nudge['content'])
        tool_steps = {e['step'] for e in result['trace'] if e['event'] == 'tool'}
        self.assertEqual(tool_steps, {1, 3})  # tool call ở hai model step khác nhau

    def test_fix_removes_h6_mechanical_violation(self):
        # Đối chiếu bằng chính hàm chấm cơ học của eval (không sửa grading.py).
        case = next(c for c in json.loads((ROOT / 'evals' / 'cases.json').read_text(encoding='utf-8'))
                    if c['id'] == 'H6')
        status = tool_call('s', 'get_status', '{"provider":"github","component":"Actions"}')
        status['calls'] += details('h02n9572mx9y', 'a')['calls']
        status['message']['tool_calls'] += details('h02n9572mx9y', 'a')['message']['tool_calls']
        # Trước cải tiến: hai tool cùng step 1 rồi trả lời -> vi phạm min_tool_steps.
        before_like = {'trace': [{'event': 'tool', 'step': 1, 'name': 'get_status'},
                                 {'event': 'tool', 'step': 1, 'name': 'get_incident_details'}], 'answer': ''}
        self.assertTrue(mechanical_checks(case, before_like)['violations'])
        fake = FakeProvider([status, final('Trả lời. ' + URL_H02N),
                             details('xr3myzj7ms51', 'b'), final('Trả lời đủ. ' + URL_H02N + ' ' + URL_XR3M)])
        result = run_agent(case['question'], fake, StatusTools(), max_steps=8)
        self.assertEqual(mechanical_checks(case, result)['violations'], [])

    def test_no_nudge_when_linked_incident_already_read(self):
        both = details('h02n9572mx9y', 'a')
        both['calls'] += details('xr3myzj7ms51', 'b')['calls']
        both['message']['tool_calls'] += details('xr3myzj7ms51', 'b')['message']['tool_calls']
        fake = FakeProvider([both, final('Xong. ' + URL_H02N)])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(events(result, 'validation_unfollowed_link'), [])

    def test_no_nudge_for_incident_without_links(self):
        url = TOOLS.get_incident_details('github', 't89g2q1800fr')['url']
        fake = FakeProvider([details('t89g2q1800fr', 'a'), final('Xong. ' + url)])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(events(result, 'validation_unfollowed_link'), [])

    def test_nudge_only_once_if_model_ignores_it(self):
        fake = FakeProvider([details('h02n9572mx9y', 'a'), final('Lần 1 ' + URL_H02N),
                             final('Lần 2 vẫn bỏ qua ' + URL_H02N)])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(events(result, 'validation_unfollowed_link')), 1)
        self.assertTrue(result['answer'].startswith('Lần 2'))

    def test_no_nudge_when_not_enough_steps(self):
        # step 2 + 2 > max_steps 3: không đủ chỗ gọi tool rồi trả lời, nên giữ completed.
        fake = FakeProvider([details('h02n9572mx9y', 'a'), final('Xong. ' + URL_H02N)])
        result = run_agent('x', fake, StatusTools(), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(events(result, 'validation_unfollowed_link'), [])

    def test_failed_read_of_linked_incident_is_not_retried(self):
        # Đã thử đọc incident được dẫn tới nhưng lỗi: không nhắc đọc lại.
        fake = FakeProvider([details('h02n9572mx9y', 'a'), details('xr3myzj7ms51', 'b'),
                             final('Không đọc được incident liên quan. ' + URL_H02N)])
        tools = StatusTools()
        original = tools.execute
        tools.execute = lambda name, args: ({'ok': False, 'error': 'TimeoutError', 'message': 'giả lập'}
                                            if args.get('incident_id') == 'xr3myzj7ms51' else original(name, args))
        result = run_agent('x', fake, tools, max_steps=6)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(events(result, 'validation_unfollowed_link'), [])


if __name__ == '__main__':
    unittest.main()
