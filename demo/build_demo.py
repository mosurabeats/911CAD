#!/usr/bin/env python3
"""Bundle the real CAD screens + the in-browser stand-in server into one self-contained demo page.

    python demo/build_demo.py OUTPUT.html

The demo uses a fictional agency and data, keeps everything in memory, and never contacts a server.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


css = read("static", "app.css")
light = re.search(r':root\[data-theme="light"\] \{(.*?)\}', css, re.S).group(1)
demo_css = f"""
/* Demo page: follow the viewer's light/dark setting when no explicit choice is made. */
@media (prefers-color-scheme: light) {{ :root:not([data-theme="dark"]) {{{light}}} }}
html, body {{ height: 100%; }}
body {{ display: flex; flex-direction: column; }}
#app {{ flex: 1; min-height: 0; }}
.shell {{ height: 100%; }}
.demo-ribbon {{
  background: var(--warn); color: #111; font-weight: 600; font-size: 12px; text-align: center;
  padding-block: 4px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
}}
header.top, .officerbar, .view {{
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
}}
.board {{ padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }}
.modal-bg {{ padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }}
.drawer {{ padding-right: env(safe-area-inset-right, 0px); }}
.view table, .panel > .body {{ overflow-x: auto; }}
"""

page = f"""<title>Interim CAD Demo</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="robots" content="noindex, nofollow">
<style>
{css}
{demo_css}
</style>
<div class="demo-ribbon" role="note">DEMO · fictional agency and data · nothing is saved · do not enter real information</div>
<div id="app"><div class="loading">Loading…</div></div>
<div id="toast" role="status" aria-live="polite"></div>
<script>
{read("demo", "mock.js")}
</script>
<script>
{read("static", "app.js")}
</script>
"""
if "</script" in read("demo", "mock.js") + read("static", "app.js"):
    sys.exit("Refusing to inline: a script contains '</script'.")
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "cad-demo.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(page)
print(f"Wrote {out} ({len(page):,} bytes)")
