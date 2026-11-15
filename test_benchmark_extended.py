"""Rebuild references and assets offline; the original holdout stays byte-identical."""

import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from benchmark.extended.__main__ import DATA, build
from benchmark import runner


class ExtendedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = build()

    def test_generated_files_match_checked_in_data(self):
        for name, data in self.generated.items():
            self.assertEqual((DATA / name).read_bytes(), data, name)

    def test_shape_and_answers(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
            cases = runner.load_cases(DATA / 'cases.jsonl', 'dev')
        self.assertEqual(len(cases), 28)
        self.assertEqual(len({c['id'] for c in cases}), 28)
        self.assertEqual(sum(bool(c.get('images')) for c in cases), 6)
        self.assertEqual(sum(not c['prompt'] for c in cases), 2)
        self.assertEqual(sum(len(c.get('images', [])) for c in cases), 7)
        for case in cases:
            self.assertEqual(case['source'], 'original-generated')
            answer = case['answer']
            text = answer['accepted'][0] if answer['type'] == 'exact' else json.dumps(answer['value'])
            self.assertTrue(runner.grade(text, answer), case['id'])

    def test_plan_does_not_call_paid_vision_even_for_image_only(self):
        cases = runner.load_cases(DATA / 'cases.jsonl', 'dev')
        config = runner.read_json(runner.ROOT / 'config.example.json')
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')) as network:
            planned = runner.plan(cases, config)
        self.assertEqual(planned['model_answers'], 84)
        self.assertGreater(planned['total_planning_rub'], 0)
        network.assert_not_called()

    def test_old_dataset_unchanged_and_disjoint(self):
        original = runner.ROOT / 'data/cases.jsonl'
        lock = runner.read_json(Path(__file__).parent / 'benchmark/extended/original-data-lock.json')
        for relative, expected in lock.items():
            self.assertEqual(hashlib.sha256((runner.ROOT / relative).read_bytes()).hexdigest(), expected)
        old = {json.loads(line)['id'] for line in original.read_text().splitlines()}
        new = {json.loads(line)['id'] for line in self.generated['cases.jsonl'].decode().splitlines()}
        self.assertFalse(old & new)
        self.assertEqual(sum(json.loads(line)['split'] == 'test' for line in original.read_text().splitlines()), 15)


if __name__ == '__main__':
    unittest.main()
