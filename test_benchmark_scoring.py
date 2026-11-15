"""Offline regression and safety tests for benchmark scoring (stdlib only)."""

import json
import unittest

from benchmark.scoring import (
    SCORING_VERSION, grade, normalized_grade, normalized_text, strict_equal,
)

EXACT = {"type": "exact", "accepted": ["47"]}


class StrictScoringTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(SCORING_VERSION, "strict-v1+outer-wrapper-v1")

    def test_exact_strip_only(self):
        for text in ("47", " 47\n", "\t47\r\n"):
            self.assertTrue(grade(text, EXACT))
        for text in ("**47**", "`47`", "047", "47.", "Answer: 47", "explain/47"):
            self.assertFalse(grade(text, EXACT), text)
        self.assertFalse(grade("47", {"type": "exact", "accepted": [" 47 "]}))

    def test_recursive_type_strictness(self):
        pairs = [(True, 1), (False, 0), (1, 1.0), ([1], [1.0]),
                 ({"a": [True]}, {"a": [1]}), ({"a": 1}, {"a": 1, "b": 2}),
                 ([1, 2], [2, 1]), ([1], [1, 2]), (None, "null")]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertFalse(strict_equal(left, right))
                self.assertFalse(grade(json.dumps(left), {"type": "json", "value": right}))
        self.assertTrue(strict_equal({"b": [None, True, 1.0], "a": 1},
                                     {"a": 1, "b": [None, True, 1.0]}))

    def test_json_requires_complete_document(self):
        answer = {"type": "json", "value": {"x": 47}}
        self.assertTrue(grade('  {"x":47}\n', answer))
        for text in ('{"x":47,"extra":0}', '{"x":47} explanation',
                     'explanation {"x":47}', '{"x":47}\n{"x":47}', '', '```json\n{"x":47}\n```'):
            self.assertFalse(grade(text, answer), text)



class NormalizationTests(unittest.TestCase):
    def test_supported_whole_wrappers(self):
        for text in ('**47**', '`47`', '```\n47\n```',
                     '```json\n47\n```', '```text\n47\n```',
                     ' \n** 47 **\t', '```json\r\n47\r\n```'):
            with self.subTest(text=text):
                self.assertEqual(normalized_text(text), '47')
                self.assertTrue(normalized_grade(text, EXACT))

    def test_json_wrappers(self):
        answer = {"type": "json", "value": {"a": [True, 1, 1.0]}}
        for wrapper in ('**%s**', '`%s`', '```json\n%s\n```', '```\n%s\n```'):
            self.assertTrue(normalized_grade(wrapper % '{"a":[true,1,1.0]}', answer))
        for payload in ('{"a":[1,1,1.0]}', '{"a":[true,1.0,1.0]}',
                        '{"a":[true,1,1.0],"extra":0}'):
            self.assertFalse(normalized_grade('```json\n' + payload + '\n```', answer))

    def test_no_answer_extraction(self):
        for text in ('explain/47', 'The answer is 47', 'Answer: **47**',
                     '**47** because arithmetic', '`47` explanation',
                     '```json\n47\n```\nExplanation',
                     'Explanation\n```text\n47\n```',
                     '**explain/47**', '```text\nexplain/47\n```'):
            self.assertFalse(normalized_grade(text, EXACT), text)

    def test_removes_only_one_layer(self):
        pairs = [('**`47`**', '`47`'), ('`**47**`', '**47**'),
                 ('```text\n**47**\n```', '**47**'),
                 ('**```\n47\n```**', '```\n47\n```')]
        for text, expected in pairs:
            self.assertEqual(normalized_text(text), expected)
            self.assertFalse(normalized_grade(text, EXACT))
        nested = '`47`'
        for _ in range(47):
            nested = '**' + nested + '**'
        self.assertEqual(normalized_text(nested), nested)
        self.assertFalse(normalized_grade(nested, EXACT))

    def test_rejects_unsafe_or_partial_wrappers(self):
        texts = ['**47', '47**', '`47', '47`', '**47** **48**', '****47****',
                 '**47**\n**48**', '`47\n`', '`47\r`', '``47``',
                 '```json 47```', '```json {"x":47}```',
                 '```json\n47```', '```json 47\n```', '```47```',
                 '```python\n47\n```', '```JSON\n47\n```',
                 '```\n47\n```\n```\n47\n```',
                 '```json\n```text\n47\n```\n```',
                 '```text\n47 ``` extra\n```',
                 '````\n47\n````', '```\n47\n``` extra']
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(normalized_text(text), text)
                self.assertFalse(normalized_grade(text, EXACT))

    def test_plain_text_is_only_trimmed(self):
        for text in ('47', 'explain/47', 'a **bold** word', '', ' \n47\t'):
            self.assertEqual(normalized_text(text), text.strip())

    def test_multiline_content_is_preserved_not_searched(self):
        payload = '{\n  "answer": 47\n}'
        self.assertEqual(normalized_text('```json\n' + payload + '\n```'), payload)
        self.assertTrue(normalized_grade('```json\n' + payload + '\n```',
                                         {"type": "json", "value": {"answer": 47}}))
        self.assertFalse(normalized_grade('```text\n47\nexplanation\n```', EXACT))


if __name__ == "__main__":
    unittest.main()
