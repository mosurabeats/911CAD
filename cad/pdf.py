"""Minimal PDF writer (Python standard library only) for printable call records and daily logs.

Produces plain-text PDFs in Courier on US Letter paper, with a header on every page
(title, page X of Y) and a CJI handling footer. Characters outside Windows-1252 are replaced.
"""
import re
import textwrap

LETTER = (612, 792)
MARGIN = 48
FONT_SIZE = 9
LEADING = 11.5
CHAR_W = 0.6 * FONT_SIZE  # Courier advance width

_SUBST = {"\u2192": "->", "\u2190": "<-", "\u2713": "v", "\u2264": "<=", "\u2265": ">="}
_NARRATIVE = re.compile(r"^  \d{4}-\d\d-\d\d \d\d:\d\d:\d\d  ")


def _enc(s):
    for k, v in _SUBST.items():
        s = s.replace(k, v)
    b = s.encode("cp1252", errors="replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def wrap_lines(lines, width):
    out = []
    for line in lines:
        line = line.rstrip().expandtabs(4)
        if len(line) <= width:
            out.append(line)
            continue
        if _NARRATIVE.match(line):
            indent = 37  # where the narrative text column starts in the text record
        else:
            indent = len(line) - len(line.lstrip()) + 2
        indent = min(indent, width // 2)
        out.extend(textwrap.wrap(line, width=width, subsequent_indent=" " * indent,
                                 drop_whitespace=True, break_long_words=True) or [""])
    return out


def text_pdf(lines, title, landscape=False, footer="Contains criminal justice information. Handle per CJIS policy."):
    """Render lines of text to PDF bytes. Long lines are wrapped; pages break automatically."""
    w, h = (LETTER[1], LETTER[0]) if landscape else LETTER
    cols = int((w - 2 * MARGIN) / CHAR_W)
    rows = int((h - 2 * MARGIN - 2 * LEADING * 2) / LEADING)
    body = wrap_lines(lines, cols)
    pages = [body[i:i + rows] for i in range(0, len(body), rows)] or [[]]

    objs = []  # index 0 -> object 1

    def add(data):
        objs.append(data)
        return len(objs)

    catalog = add(None)
    pages_obj = add(None)
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    font_b = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for n, page_lines in enumerate(pages, 1):
        top = h - MARGIN
        s = [b"BT", b"/F2 10 Tf", b"%d %d Td" % (MARGIN, top - 10), b"(" + _enc(title) + b") Tj", b"ET",
             b"BT", b"/F1 8 Tf", b"%d %d Td" % (w - MARGIN - int(CHAR_W * 8 / 9 * len(f"Page {n} of {len(pages)}")), top - 10),
             b"(" + _enc(f"Page {n} of {len(pages)}") + b") Tj", b"ET",
             b"0.5 w %d %d m %d %d l S" % (MARGIN, top - 16, w - MARGIN, top - 16),
             b"BT", b"/F1 %d Tf" % FONT_SIZE, b"%.1f TL" % LEADING, b"%d %d Td" % (MARGIN, top - 16 - LEADING * 2)]
        for line in page_lines:
            s.append(b"(" + _enc(line) + b") Tj T*")
        s += [b"ET", b"BT", b"/F1 7 Tf", b"%d %d Td" % (MARGIN, MARGIN - 18), b"(" + _enc(footer) + b") Tj", b"ET"]
        stream = b"\n".join(s)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R"
                        b" /Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
                        % (pages_obj, w, h, content, font, font_b)))
    objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj
    objs[pages_obj - 1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
                           + b"] /Count %d >>" % len(kids))
    info = add(b"<< /Title (" + _enc(title) + b") /Producer (Interim CAD) >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, data in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + data + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, catalog, info, xref)
    return bytes(out)
