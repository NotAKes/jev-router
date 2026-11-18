"""Build the Windows distribution from reviewed root sources (stdlib only)."""
import argparse
import io
from pathlib import Path
import re
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parent
ARCHIVE_NAME = "Jev_OpenCode_Windows.zip"
README_SOURCE = "WINDOWS_AUTOSTART_JEV_NO_ANTHROPIC.md"
SOURCE_FILES = (
    "start_jev_no_anthropic.bat",
    "setup_opencode_jev.bat",
    "setup_opencode_jev.py",
    "jev_router.py",
)
# A safety net for known Polza token formats, not a substitute for reviewing sources.
PLACEHOLDERS = {"sk-polza-PASTE-YOUR-KEY-HERE", "sk-polza-..."}
POLZA_TOKEN = re.compile(r"(?:sk-polza-|pza_)[a-z0-9_-]+", re.IGNORECASE)
BAT_KEY = re.compile(r'^\s*@?set\s+"?POLZA_API_KEY\s*=([^"\r\n]*)', re.I | re.M)
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def read_source(root, name):
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular source file: {name}")
    data = path.read_bytes()
    text = data.decode("utf-8-sig")
    if any(match[0] not in PLACEHOLDERS for match in POLZA_TOKEN.finditer(text)):
        raise ValueError(f"Possible Polza credential in {name}; refusing to package it")
    if name.endswith(".bat"):
        text = crlf(text)
        for assignment in BAT_KEY.finditer(text):
            value = assignment[1].strip()
            if value and value not in PLACEHOLDERS and value.upper() != "%POLZA_API_KEY%":
                raise ValueError(f"Hardcoded POLZA_API_KEY in {name}; refusing to package it")
        return text.encode("utf-8")
    return text if name == README_SOURCE else data


def crlf(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def readme_lines(markdown):
    """Render the headings, fenced code and simple inline markup used in the guide."""
    fenced = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            yield "code", line
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        kind = "title" if heading and len(heading[1]) == 1 else "heading" if heading else "text"
        text = heading[2] if heading else line
        # Keep command snippets literal, even if they contain Markdown punctuation.
        spans = re.split(r"(`+[^`]+`+)", text)
        for index, span in enumerate(spans):
            if index % 2:
                spans[index] = span.strip("`")
            else:
                spans[index] = re.sub(
                    r"\[([^\]]+)\]\(([^)]+)\)",
                    lambda match: match[1] if match[1] == match[2] else f"{match[1]} ({match[2]})",
                    span,
                ).replace("**", "")
        yield kind, "".join(spans)


def zip_entry(archive, name, data):
    # Do not copy local timestamps, permissions, extended attributes, or paths.
    info = ZipInfo(name)
    info.create_system = 0
    archive.writestr(info, data, compress_type=ZIP_DEFLATED)


def make_docx(lines):
    def word_element(parent, tag, **attrs):
        return ET.SubElement(parent, f"w:{tag}", {f"w:{k}": v for k, v in attrs.items()})

    document = ET.Element("w:document", {"xmlns:w": WORD})
    body = word_element(document, "body")
    for kind, line in lines:
        run = word_element(word_element(body, "p"), "r")
        if kind != "text":
            props = word_element(run, "rPr")
            if kind == "code":
                word_element(props, "rFonts", ascii="Consolas", hAnsi="Consolas")
            else:
                word_element(props, "b")
                word_element(props, "sz", val="36" if kind == "title" else "28")
        for index, chunk in enumerate(line.split("\t")):
            if index:
                word_element(run, "tab")
            text = word_element(run, "t")
            text.set("xml:space", "preserve")
            text.text = chunk

    xml = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    # ElementTree will serialize some invalid XML control characters as-is.
    ET.fromstring(xml)
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as docx:
        zip_entry(docx, "[Content_Types].xml", b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>''')
        zip_entry(docx, "_rels/.rels", b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>''')
        zip_entry(docx, "word/document.xml", xml)
    return buffer.getvalue()


def build_release(root, output):
    files = {name: read_source(root, name) for name in SOURCE_FILES}
    markdown = read_source(root, README_SOURCE)
    lines = list(readme_lines(markdown))
    text = "\n".join(line for _, line in lines) + "\n"
    files["README.md"] = markdown.encode("utf-8")
    files["README.txt"] = crlf(text).encode("utf-8-sig")
    files["README.docx"] = make_docx(lines)

    # Write beside the destination so a failed build cannot truncate the old ZIP.
    with tempfile.TemporaryDirectory(dir=output.parent) as temp_dir:
        temp_zip = Path(temp_dir) / ARCHIVE_NAME
        with ZipFile(temp_zip, "w") as archive:
            for name, data in files.items():
                zip_entry(archive, name, data)
        temp_zip.replace(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=ROOT / ARCHIVE_NAME,
                        help=f"destination ZIP (default: {ARCHIVE_NAME} beside this script)")
    args = parser.parse_args()
    if args.output.suffix.lower() != ".zip":
        parser.error("--output must be a .zip file")
    try:
        build_release(ROOT, args.output)
    except (OSError, ValueError, ET.ParseError) as error:
        parser.exit(1, f"Could not build release: {error}\n")
    print(f"Built {args.output}")


if __name__ == "__main__":
    main()
