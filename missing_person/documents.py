"""Fetch and read the source documents a case cites.

WHY THIS EXISTS. A case file listed the agency's NCIC bulletin as a URL from
the very first version, and nobody opened it for a full day of analysis. That
PDF contained the only specific street address any source gave, the exact time
of last contact, and the explanation for a date that had been written off as a
data-entry quirk. Every reconciliation attempt made before reading it was
working without the one document that answered the question.

A link is not a read. So documents are fetched, extracted to text, and their
unread state is made LOUD rather than silent: `mp scan` pulls anything new and
refuses to present a case as reviewed while a cited document has never been
opened.

Text extraction prefers `pdftotext` (poppler) when it is on PATH, because it
handles layout properly. The fallback is pure stdlib, and getting it right
mattered more than expected.

A naive fallback that inflates the streams and reads the parenthesised strings
returns CONFIDENT GARBAGE on any PDF with a subset font. The agency bulletin
this was built for embeds a Type0 font whose glyph codes are two bytes and
sit at a fixed offset from ASCII, so a byte-level read produced 14KB of text
that looked like text, contained not one of the facts in the document, and
raised no error. A fallback that fails loudly is fine; one that lies is worse
than having none.

So the fallback parses the font's ToUnicode CMap (bfchar and bfrange) and maps
glyph codes through it, and `looks_like_text` rejects output that is mostly
unmappable characters. The repo keeps zero Python package dependencies.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path

from missing_person.net import SourceError, fetch

TEXT_OPS = re.compile(rb"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>")
BFCHAR = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
BFRANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
HEXPAIR = re.compile(rb"<([0-9A-Fa-f]+)>")
ESCAPES = {b"\\n": b"\n", b"\\r": b"\r", b"\\t": b"\t", b"\\(": b"(",
           b"\\)": b")", b"\\\\": b"\\"}


@dataclass(frozen=True)
class Document:
    label: str
    url: str
    kind: str = "pdf"
    note: str = ""

    @property
    def slug(self) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", self.label.lower()).strip("-")
        return base or hashlib.sha1(self.url.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class DocumentState:
    document: Document
    path: Path
    text_path: Path

    @property
    def fetched(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    @property
    def extracted(self) -> bool:
        return self.text_path.exists() and self.text_path.stat().st_size > 0

    @property
    def unread(self) -> bool:
        return not self.extracted

    def text(self) -> str:
        return self.text_path.read_text(errors="replace") if self.extracted else ""


def store_dir(case_id: str, cases_dir: Path) -> Path:
    """Documents live beside the case file, outside any repo.

    They are case material about a real person, same as the case file itself.
    """
    return cases_dir.parent / "documents" / case_id


def _pdftotext(path: Path) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-layout", str(path), "-"],
                             capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = out.stdout.decode("utf-8", "replace").strip()
    return text or None


def _unescape(chunk: bytes) -> bytes:
    for old, new in ESCAPES.items():
        chunk = chunk.replace(old, new)
    return chunk


def _streams(data: bytes) -> list[bytes]:
    out: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        blob = match.group(1)
        try:
            blob = zlib.decompress(blob)
        except zlib.error:
            pass  # some streams are stored uncompressed
        out.append(blob)
    return out


def _tounicode(streams: list[bytes]) -> dict[int, str]:
    """Glyph code -> character, from the font's ToUnicode CMap.

    Without this a subset font reads as a Caesar cipher of the real text.
    """
    cmap: dict[int, str] = {}
    for blob in streams:
        for body in BFCHAR.findall(blob):
            pairs = HEXPAIR.findall(body)
            # strict=False on purpose: a truncated or malformed CMap should
            # yield the mappings it does have, not abort the whole extraction.
            for src, dst in zip(pairs[0::2], pairs[1::2], strict=False):
                cmap[int(src, 16)] = _utf16be(dst)
        for body in BFRANGE.findall(blob):
            hexes = HEXPAIR.findall(body)
            for lo, hi, dst in zip(hexes[0::3], hexes[1::3], hexes[2::3],
                                   strict=False):
                start, end, base = int(lo, 16), int(hi, 16), int(dst, 16)
                for offset in range(min(end - start + 1, 65536)):
                    cmap[start + offset] = chr(base + offset)
    return cmap


def _utf16be(raw_hex: bytes) -> str:
    try:
        return bytes.fromhex(raw_hex.decode()).decode("utf-16-be", "replace")
    except ValueError:
        return ""


def _codes(token: bytes) -> bytes:
    if token.startswith(b"<"):
        digits = re.sub(rb"\s", b"", token[1:-1])
        if len(digits) % 2:
            digits += b"0"
        return bytes.fromhex(digits.decode())
    return _unescape(token[1:-1])


def looks_like_text(text: str) -> bool:
    """Reject output that is not resolved prose.

    The guard that would have caught the subset-font bug, arrived at after two
    wrong attempts that are worth recording, because both looked reasonable:

    1. Ratio of alphanumerics-plus-punctuation. Failed: a glyph dump is built
       from the same symbols ordinary punctuation uses, so nearly everything
       counted as "plain".
    2. Mean word length. Failed: the unresolved two-byte encoding leaves a
       literal NUL between glyphs, not a space, so `split()` saw a handful of
       enormous tokens and the average sailed past any threshold.

    What actually separates them is the NUL itself. Extracted text contains no
    C0 control characters beyond tab, newline and carriage return; an
    unresolved two-byte encoding is roughly half NUL by construction. Letter
    share is kept as a second signal so a page of pure digits, which is also
    not a successful decode, does not pass.
    """
    if len(text) < 20:
        return False
    control = sum(1 for c in text if ord(c) < 32 and c not in "\t\n\r")
    if control / len(text) > 0.02:
        return False
    visible = [c for c in text if not c.isspace()]
    if len(visible) < 20:
        return False
    return sum(1 for c in visible if c.isalpha()) / len(visible) >= 0.45


def _pdf_stdlib(data: bytes) -> str:
    """Inflate the streams, map glyph codes through ToUnicode, read the text."""
    streams = _streams(data)
    cmap = _tounicode(streams)
    pieces: list[str] = []
    for blob in streams:
        if b"Tj" not in blob and b"TJ" not in blob:
            continue
        for token in TEXT_OPS.findall(blob):
            raw = _codes(token)
            if cmap:
                # Type0 fonts use two-byte codes. Reading them as single bytes
                # is what produced the fixed-offset garbage.
                pieces.append("".join(
                    cmap.get(int.from_bytes(raw[i:i + 2], "big"), "")
                    for i in range(0, len(raw) - 1, 2)))
            else:
                pieces.append(raw.decode("latin-1", "replace"))
    text = re.sub(r"\s{2,}", " ", " ".join(pieces)).strip()
    return text


def extract(path: Path) -> str:
    """Text of a document, by the best method available."""
    if path.suffix.lower() != ".pdf":
        return path.read_text(errors="replace")
    via_tool = _pdftotext(path)
    if via_tool:
        return via_tool
    text = _pdf_stdlib(path.read_bytes())
    if not text:
        raise SourceError(
            f"{path.name}: no text extracted. It may be a scanned image, which "
            "needs OCR rather than text extraction. Open it by hand."
        )
    if not looks_like_text(text):
        raise SourceError(
            f"{path.name}: extracted output does not look like text, so the "
            "font encoding was probably not resolved. Install poppler "
            "(`brew install poppler`) for pdftotext, or open it by hand. "
            "Refusing to return output that cannot be trusted."
        )
    return text


def state(document: Document, case_id: str, cases_dir: Path) -> DocumentState:
    directory = store_dir(case_id, cases_dir)
    suffix = ".pdf" if document.kind == "pdf" else ".txt"
    return DocumentState(document, directory / f"{document.slug}{suffix}",
                         directory / f"{document.slug}.txt")


def ensure(document: Document, case_id: str, cases_dir: Path,
           refetch: bool = False) -> DocumentState:
    """Download and extract a document if it is not already on disk."""
    st = state(document, case_id, cases_dir)
    st.path.parent.mkdir(parents=True, exist_ok=True)
    if refetch or not st.fetched:
        st.path.write_bytes(fetch(document.url, timeout=90))
    if refetch or not st.extracted:
        st.text_path.write_text(extract(st.path))
    return st
