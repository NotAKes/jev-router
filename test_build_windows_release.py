"""Offline Windows packaging tests: python3 -m unittest -v test_build_windows_release."""
import codecs
import io
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import build_windows_release as release


class BuildWindowsReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = self.root / release.ARCHIVE_NAME
        (self.root / "start_jev_no_anthropic.bat").write_bytes(
            b'@echo off\r\nset "POLZA_API_KEY=sk-polza-PASTE-YOUR-KEY-HERE"\necho Start\r'
        )
        (self.root / "setup_opencode_jev.bat").write_bytes(b"@echo off\n")
        (self.root / "setup_opencode_jev.py").write_bytes(b"# Setup\n")
        (self.root / "jev_router.py").write_bytes(b"# Router\n")
        (self.root / release.README_SOURCE).write_bytes(
            '# Настройка\n\n**Запусти** `py -3` & [сайт](https://example.test).\n'
            '```bat\n\techo <example> **\n```\n'.encode()
        )

    def build(self):
        release.build_release(self.root, self.output)

    def test_archive_contains_only_reviewed_sources_and_generated_readmes(self):
        for name in (".env", ".DS_Store", "README.txt", "README.docx"):
            (self.root / name).write_bytes(b"local data")
        staging = self.root / "Jev_OpenCode_Windows"
        staging.mkdir()
        (staging / "start_jev_no_anthropic.bat").write_bytes(b"stale copy")
        self.output.write_bytes(b"old zip")
        self.build()

        with ZipFile(self.output) as archive:
            self.assertEqual(archive.namelist(), [
                "start_jev_no_anthropic.bat", "setup_opencode_jev.bat",
                "setup_opencode_jev.py", "jev_router.py", "README.md", "README.txt", "README.docx",
            ])
            self.assertEqual(archive.read("start_jev_no_anthropic.bat"),
                             b'@echo off\r\nset "POLZA_API_KEY=sk-polza-PASTE-YOUR-KEY-HERE"\r\n'
                             b'echo Start\r\n')
            self.assertEqual(archive.read("setup_opencode_jev.bat"), b"@echo off\r\n")
            self.assertEqual(archive.read("jev_router.py"), b"# Router\n")
            self.assertTrue(all(not info.extra and not info.comment for info in archive.infolist()))
            txt = archive.read("README.txt")
            self.assertTrue(txt.startswith(codecs.BOM_UTF8))
            self.assertEqual(txt.decode("utf-8-sig"),
                             "Настройка\r\n\r\nЗапусти py -3 & сайт (https://example.test).\r\n"
                             "\techo <example> **\r\n")
            with ZipFile(io.BytesIO(archive.read("README.docx"))) as docx:
                self.assertEqual(set(docx.namelist()), {
                    "[Content_Types].xml", "_rels/.rels", "word/document.xml",
                })
                for name in docx.namelist():
                    ET.fromstring(docx.read(name))
                document = ET.fromstring(docx.read("word/document.xml"))
                ns = {"w": release.WORD}
                self.assertEqual([element.text for element in document.findall(".//w:t", ns)],
                                 ["Настройка", None, "Запусти py -3 & сайт (https://example.test).",
                                  None, "echo <example> **"])
                self.assertIsNotNone(document.find(".//w:tab", ns))

    def test_repeatable_archive(self):
        self.build()
        first = self.output.read_bytes()
        self.build()
        self.assertEqual(self.output.read_bytes(), first)

    def test_rejects_credentials_without_replacing_existing_archive(self):
        self.output.write_bytes(b"old zip")
        for name, secret in (
            ("start_jev_no_anthropic.bat", b'set "POLZA_API_KEY=example-hardcoded-token"'),
            ("jev_router.py", b'# example sk-polza-example-hardcoded-token'),
            (release.README_SOURCE, b'Example pza_example_fake_token'),
        ):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(secret)
                with self.assertRaisesRegex(ValueError, name) as caught:
                    self.build()
                self.assertNotIn(secret.decode(), str(caught.exception))
                self.assertEqual(self.output.read_bytes(), b"old zip")
                path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
