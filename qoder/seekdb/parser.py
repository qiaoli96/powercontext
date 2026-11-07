import re
from dataclasses import dataclass


@dataclass
class Paragraph:
    file: str
    section: str
    content: str


def parse_markdown_file(filepath: str) -> list[Paragraph]:
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    filename = filepath.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    paragraphs: list[Paragraph] = []
    current_section = ""
    current_block: list[str] = []

    def flush_block():
        nonlocal current_block
        block_text = "\n".join(current_block).strip()
        if block_text:
            paragraphs.append(Paragraph(
                file=filename,
                section=current_section,
                content=block_text,
            ))
        current_block = []

    for line in text.split("\n"):
        heading = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading:
            flush_block()
            current_section = heading.group(2).strip()
        elif line.strip() == "":
            flush_block()
        else:
            current_block.append(line)

    flush_block()
    return paragraphs
