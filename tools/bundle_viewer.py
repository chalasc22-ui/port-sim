"""Bundle viewer3d/index.html with its data into one file that opens without a web server.

Run from the repo root:

    python tools/bundle_viewer.py                    # viewer3d/dames-point-3d.html, a full page
    python tools/bundle_viewer.py out.html --fragment  # no <html>/<head>/<body> wrapper, for hosts that add their own

viewer3d/index.html fetches layouts/dames-point.compiled.json and viewer3d/demo-routes.json at run
time (serve the repo root with `python -m http.server`). The bundle embeds both as window.PORT_DATA,
so it opens by double-click and can be shared as one file. Three.js still loads from jsDelivr.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    fragment = "--fragment" in argv
    args = [a for a in argv[1:] if a != "--fragment"]
    out = Path(args[0]) if args else ROOT / "viewer3d" / "dames-point-3d.html"
    page = (ROOT / "viewer3d" / "index.html").read_text(encoding="utf-8")
    data = {
        "layout": json.loads((ROOT / "layouts" / "dames-point.compiled.json").read_text(encoding="utf-8")),
        "routes": json.loads((ROOT / "viewer3d" / "demo-routes.json").read_text(encoding="utf-8")),
    }
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    page = page.replace("<!-- BUNDLE:DATA -->", f"<script>window.PORT_DATA = {blob};</script>")
    if fragment:
        head = page.split("<!-- BUNDLE:HEAD-START -->")[1].split("<!-- BUNDLE:HEAD-END -->")[0]
        body = page.split("<!-- BUNDLE:BODY-START -->")[1].split("<!-- BUNDLE:BODY-END -->")[0]
        page = head.strip() + "\n" + body.strip() + "\n"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
