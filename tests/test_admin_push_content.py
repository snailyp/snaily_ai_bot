"""Frozen content plans; no bot, app configuration, data directories or network."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.services.admin_push import PushError, content


def utf16(text):
    return len(text.encode("utf-16-le")) // 2


def entity_text(part, entity):
    encoded = part["text"].encode("utf-16-le")
    return encoded[entity["offset"] * 2:(entity["offset"] + entity["length"]) * 2].decode("utf-16-le")


class CompositionTests(unittest.TestCase):
    def test_empty_draft_and_required_formal_content_and_targets(self):
        self.assertEqual(content.normalize_composition({}, allow_empty=True),
                         {"text": "", "asset_ids": [], "targets": [], "settings": {}})
        for composition in ({}, {"text": "正文"}, {"targets": ["@channel"]}, {"text": "  \n", "targets": ["1"]}):
            with self.subTest(composition=composition), self.assertRaises(PushError):
                content.normalize_composition(composition)
        self.assertEqual(content.normalize_composition({"asset_ids": ["asset"], "targets": ["-100123"]})["text"], "")

    def test_targets_use_helper_then_casefold_and_numeric_dedup(self):
        with patch.object(content.helpers, "parse_chat_ids", wraps=content.helpers.parse_chat_ids) as parse:
            value = content.normalize_composition({"text": "内容", "targets": " @Channel, @channel, -100123, -100123, 0012, 12, @other_name "})
        parse.assert_called_once()
        self.assertEqual(value["targets"], ["@Channel", "-100123", "12", "@other_name"])
        self.assertEqual(content.normalize_composition({"text": "x", "targets": 123})["targets"], ["123"])

    def test_invalid_targets_rejected_even_for_drafts(self):
        for targets in (True, {}, [None], [True], ["@abc"], ["https://t.me/channel"], ["@bad-name"],
                        ["0"], ["-0"], ["-9223372036854775809"], ["1,2"], ["@channel\nmalicious"], ["1" * 100]):
            with self.subTest(targets=targets), self.assertRaises(PushError):
                content.normalize_composition({"targets": targets}, allow_empty=True)

    def test_assets_and_settings_are_validated_and_copied(self):
        value = {"text": "正文", "asset_ids": ["a", "b", "c", "d"], "targets": ["@channel"],
                 "settings": {"tone": "friendly", "custom": "轻松", "material": "材料", "length": "500", "image_prompt": "山"}}
        result = content.normalize_composition(value)
        self.assertEqual(result, value)
        self.assertIsNot(result["asset_ids"], value["asset_ids"])
        self.assertIsNot(result["settings"], value["settings"])
        for change in ({"asset_ids": ["a"] * 2}, {"asset_ids": list("abcde")}, {"asset_ids": "a"},
                       {"asset_ids": ["../outside"]}, {"text": None}, {"text": "\ud800"}, {"text": "a\x00b"},
                       {"settings": []}, {"settings": {"tone": "unknown"}}, {"settings": {"material": {}}},
                       {"settings": {"length": True}}, {"settings": {"length": -1}}, {"settings": {"secret": "x"}}):
            with self.subTest(change=change), self.assertRaises(PushError):
                content.normalize_composition({**value, **change})

    def test_schedule_is_always_shanghai_and_future(self):
        # 2030-01-02 12:34 Beijing == 2030-01-02 04:34 UTC.
        from datetime import datetime, timezone
        expected = datetime(2030, 1, 2, 4, 34, tzinfo=timezone.utc).timestamp()
        self.assertEqual(content.parse_schedule("2030-01-02T12:34", expected - 1), expected)
        self.assertEqual(content.parse_schedule("2030-01-02T12:34:00.500", expected), expected + 0.5)
        self.assertIsNone(content.parse_schedule(None, expected))
        self.assertIsNone(content.parse_schedule("", expected))
        for value in ("2030-01-02T12:34", "2030-01-02T12:34Z", "2030-01-02T12:34+08:00", "2030-02-31T12:34",
                      "2030-01-02", "2030-01-02 12:34", expected, {}, "yesterday"):
            with self.subTest(value=value), self.assertRaises(PushError):
                content.parse_schedule(value, expected)


class FrozenPlanTests(unittest.TestCase):
    def assert_valid_plan(self, plan):
        for part in plan:
            self.assertEqual(set(part), {"kind", "text", "entities", "asset_ids"})
            limit = content.TEXT_LIMIT if part["kind"] == "text" else content.CAPTION_LIMIT
            self.assertLessEqual(utf16(part["text"]), limit)
            for entity in part["entities"]:
                self.assertGreater(entity["length"], 0)
                self.assertGreaterEqual(entity["offset"], 0)
                self.assertLessEqual(entity["offset"] + entity["length"], utf16(part["text"]))
                self.assertTrue(entity_text(part, entity))  # Also rejects split surrogate pairs.

    def test_whitespace_only_chunks_rejected_during_preview_without_truncating(self):
        for text in (" " * 5000 + "正文", "开头" + " " * 9000 + "结尾", "正文" + " " * 5000):
            for ids in ([], ["image"]):
                with self.subTest(text_length=len(text), images=ids), self.assertRaises(PushError):
                    content.compile_plan(text, ids)
        plan = content.compile_plan(" " * 5000, ["image"])
        self.assertEqual([part["kind"] for part in plan], ["photo"])
        self.assertEqual(plan[0]["text"], "")

    def test_pure_text_photo_and_album_order(self):
        self.assertEqual(content.compile_plan("plain", []), [{"kind": "text", "text": "plain", "entities": [], "asset_ids": []}])
        for count in (1, 2, 4):
            with self.subTest(count=count):
                ids = list("abcd")[:count]
                self.assertEqual(content.compile_plan("", ids), [{"kind": "photo" if count == 1 else "album",
                                                                  "text": "", "entities": [], "asset_ids": ids}])
        for text, ids in (("", []), (" \n", []), ("** **", []), ("x", list("abcde"))):
            with self.assertRaises(PushError):
                content.compile_plan(text, ids)

    def test_caption_and_message_boundaries_measure_parsed_utf16(self):
        for text, expected_kinds in (("a" * 1024, ["photo"]), ("a" * 1025, ["text", "photo"]),
                                     ("😀" * 512, ["photo"]), ("😀" * 513, ["text", "photo"]),
                                     ("**" + "a" * 1024 + "**", ["photo"]),
                                     ("a" * 4096, ["text", "photo"]), ("a" * 4097, ["text", "text", "photo"])):
            with self.subTest(length=len(text)):
                plan = content.compile_plan(text, ["a"])
                self.assertEqual([part["kind"] for part in plan], expected_kinds)
                self.assert_valid_plan(plan)
        self.assertEqual(content.compile_plan("a" * 1025, ["a", "b"])[-1],
                         {"kind": "album", "text": "", "entities": [], "asset_ids": ["a", "b"]})

    def test_common_markdown_becomes_entities_using_existing_conversion(self):
        text = "😀 **粗体** _斜体_ ~~删除~~ `x.y` [链接](https://example.com/a?q=1)"
        with patch.object(content.helpers, "to_markdown_v2", wraps=content.helpers.to_markdown_v2) as convert:
            plan = content.compile_plan(text, [])
        convert.assert_called_once_with(text)
        self.assertEqual(plan[0]["text"], "😀 粗体 斜体 删除 x.y 链接")
        self.assertEqual([entity["type"] for entity in plan[0]["entities"]], ["bold", "italic", "strikethrough", "code", "text_link"])
        self.assertEqual(plan[0]["entities"][0], {"type": "bold", "offset": 3, "length": 2})
        self.assertEqual(plan[0]["entities"][-1]["url"], "https://example.com/a?q=1")
        self.assert_valid_plan(plan)

    def test_entities_crossing_chunks_preserve_all_text_and_whitespace(self):
        inner = "😀" * 5000
        plan = content.compile_plan("prefix **" + inner + "** suffix  \n\n", [])
        self.assertEqual("".join(part["text"] for part in plan), "prefix " + inner + " suffix  \n\n")
        self.assertEqual("".join(entity_text(part, entity) for part in plan for entity in part["entities"]), inner)
        self.assertTrue(all(entity["type"] == "bold" for part in plan for entity in part["entities"]))
        self.assert_valid_plan(plan)
        # A one-unit remainder must not consume half of an emoji.
        plan = content.compile_plan("x" * 4095 + "😀" + "y", [])
        self.assertEqual([utf16(part["text"]) for part in plan], [4095, 3])

    def test_long_preformatted_content_is_clipped_without_fence_fallback(self):
        body = "😀value = 1\n" * 600
        plan = content.compile_plan("```python\n" + body + "```", [])
        self.assertEqual("".join(part["text"] for part in plan), body)
        self.assertTrue(all(e["type"] == "pre" and e["language"] == "python" for p in plan for e in p["entities"]))
        self.assert_valid_plan(plan)

    def test_safe_nested_formatting_and_no_entities_overlapping_code(self):
        plan = content.compile_plan("**粗体 _斜体_** **`code`**", [])
        self.assertIn("**code**".replace("**", "*"), plan[0]["text"])
        self.assert_valid_plan(plan)
        for part in plan:
            for entity in part["entities"]:
                if entity["type"] in {"code", "pre"}:
                    self.assertFalse(any(other is not entity and other["offset"] <= entity["offset"]
                                         and other["offset"] + other["length"] >= entity["offset"] + entity["length"]
                                         for other in part["entities"]))

    def test_unsupported_markup_and_html_are_visible_text_without_unsafe_links(self):
        for text in ('<img src=x onerror="alert(1)">', '||spoiler||', '**unmatched', '__underline__',
                     '[bad](https://example.com/unbalanced_(path))',
                     '[bad](javascript:alert(1))', '[bad](data:text/html,test)', '[bad](file:///etc/passwd)',
                     '[bad](https://user:password@example.com/x)'):
            with self.subTest(text=text):
                plan = content.compile_plan(text, [])
                self.assertEqual(plan[0]["text"], text)
                self.assertFalse(plan[0]["entities"])
        for url in ("https://example.com", "http://example.com", "mailto:admin@example.com"):
            plan = content.compile_plan(f"[safe]({url})", [])
            self.assertEqual(plan[0]["entities"][0]["url"], url)


if __name__ == "__main__":
    unittest.main()
