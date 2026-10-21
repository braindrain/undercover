"""The policy wording and the claims manual, split into their numbered sections."""

import re
from typing import Literal

from backend import db
from backend.models import Contents, SectionHeading

DocumentName = Literal["policy_wording", "claims_manual"]

SECTION = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)


def read(document: DocumentName) -> str:
    """Return a document in full as Markdown."""
    return db.read_data(f"{document}.md")


def sections(document: DocumentName) -> dict[int, str]:
    """Return each numbered section's Markdown, from its heading, by number."""
    text = read(document)
    starts = list(SECTION.finditer(text))
    ends = [match.start() for match in starts[1:]] + [len(text)]
    return {
        int(match[1]): text[match.start() : end].strip()
        for match, end in zip(starts, ends, strict=True)
    }


def contents(document: DocumentName) -> Contents:
    """Return a document's title, its opening lines and its numbered sections."""
    text = read(document)
    headings = list(SECTION.finditer(text))
    title, _, opening = text[: headings[0].start()].partition("\n")
    return Contents(
        title=title.removeprefix("# "),
        opening=opening.strip(),
        sections=[
            SectionHeading(number=int(match[1]), title=match[2]) for match in headings
        ],
    )
