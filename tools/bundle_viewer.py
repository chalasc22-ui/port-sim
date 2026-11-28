"""Bundle viewer3d/index.html with its data into one file that opens without a web server or the internet.

Run from the repo root:

    python tools/bundle_viewer.py                      # viewer3d/dames-point-3d.html: one file, nothing else needed
    python tools/bundle_viewer.py out.html --cdn       # a full page that loads Three.js and its typefaces from the web
    python tools/bundle_viewer.py out.html --fragment  # no <html>/<head>/<body> wrapper, for hosts that add their own
    python tools/bundle_viewer.py out.html --replay viewer3d/demo-replay.json   # the six-container walkthrough instead

viewer3d/index.html fetches layouts/dames-point.compiled.json, viewer3d/demo-routes.json, a replay
(viewer3d/terminal-replay.json, the integrated terminal) and the recorded what-if runs
(viewer3d/terminal-variants.json) at run time (serve the repo root with `python -m http.server`), and
loads Three.js from jsDelivr and three typefaces from Google Fonts.

The bundle embeds the data as window.PORT_DATA, so it opens by double-click. It also carries Three.js
and the typefaces inside it, copied from viewer3d/vendor/, so it asks the network for nothing: it plays
the same with the Wi-Fi off. A fragment (and --cdn) keeps the web addresses instead: a host that wraps
the page serves those itself, and the file is a megabyte smaller. For a page with the simulator behind
it, see tools/control_room.py.

viewer3d/vendor/ holds three.min.js and OrbitControls.js (MIT) and, in fonts/, the Latin subsets of
Barlow, IBM Plex Mono and Big Shoulders Stencil Display (SIL Open Font Licence 1.1), each with its
licence beside it. `python tools/control_room.py --vendor` downloads them again.
"""

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "viewer3d" / "vendor"
# What the page loads from the web, and the copy of each kept in viewer3d/vendor/.
SCRIPTS = {
    "https://cdn.jsdelivr.net/npm/three@0.147.0/build/three.min.js": "three.min.js",
    "https://cdn.jsdelivr.net/npm/three@0.147.0/examples/js/controls/OrbitControls.js": "OrbitControls.js",
}
FONTS = [                                    # family, weight, file in viewer3d/vendor/fonts/
    ("Big Shoulders Stencil Display", 600, "big-shoulders-stencil-display-latin-600-normal.woff2"),
    ("Big Shoulders Stencil Display", 800, "big-shoulders-stencil-display-latin-800-normal.woff2"),
    ("Barlow", 400, "barlow-latin-400-normal.woff2"),
    ("Barlow", 500, "barlow-latin-500-normal.woff2"),
    ("Barlow", 600, "barlow-latin-600-normal.woff2"),
    ("IBM Plex Mono", 400, "ibm-plex-mono-latin-400-normal.woff2"),
    ("IBM Plex Mono", 500, "ibm-plex-mono-latin-500-normal.woff2"),
]
FONT_LINKS = re.compile(r'<link rel="(?:preconnect|stylesheet)" href="https://fonts\.g[^"]*"[^>]*>\n')
LOADS = re.compile(r'<(?:script|link|img|iframe|audio|video|source)\b[^>]*\b(?:src|href)="(https?:)?//[^"]*"')


def missing() -> list[str]:
    """The files of viewer3d/vendor/ that a self-contained page needs and that are not there."""
    wanted = [VENDOR / name for name in SCRIPTS.values()] + [VENDOR / "fonts" / name for _, _, name in FONTS]
    return [str(f.relative_to(ROOT)) for f in wanted if not f.exists()]


def loads_from_the_web(page: str) -> list[str]:
    """Every tag of a page that would fetch something from another host: none, in a self-contained page."""
    return [m.group(0) for m in LOADS.finditer(page)]


def self_contained(page: str) -> str:
    """The page with Three.js and its typefaces inside it, so that it asks the network for nothing."""
    for url, name in SCRIPTS.items():
        tag = f'<script src="{url}"></script>'
        if page.count(tag) != 1:
            raise ValueError(f"the page does not load {url} as expected")
        code = (VENDOR / name).read_text(encoding="utf-8").replace("</script", "<\\/script")
        page = page.replace(tag, f"<script>/* {name}, from viewer3d/vendor/ (MIT licence) */\n{code}\n</script>")
    faces = "".join(
        f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};font-display:swap;"
        f"src:url(data:font/woff2;base64,{base64.b64encode((VENDOR / 'fonts' / name).read_bytes()).decode('ascii')}) format('woff2')}}\n"
        for family, weight, name in FONTS)
    links = FONT_LINKS.findall(page)
    if len(links) != 3:
        raise ValueError("the page does not link its typefaces as expected")
    page = page.replace(links[0], f"<style>/* typefaces from viewer3d/vendor/fonts/ (SIL Open Font Licence 1.1) */\n{faces}</style>\n", 1)
    for link in links[1:]:
        page = page.replace(link, "", 1)
    left = loads_from_the_web(page)
    if left:
        raise ValueError(f"the page would still load from the web: {left[0]}")
    return page


def bundle(replay: Path, variants: Path | None, cdn: bool = False, fragment: bool = False) -> str:
    """The page as one string, with its data and (unless cdn or fragment) everything else it needs."""
    page = (ROOT / "viewer3d" / "index.html").read_text(encoding="utf-8")
    data = {
        "layout": json.loads((ROOT / "layouts" / "dames-point.compiled.json").read_text(encoding="utf-8")),
        "routes": json.loads((ROOT / "viewer3d" / "demo-routes.json").read_text(encoding="utf-8")),
        "sim": json.loads(replay.read_text(encoding="utf-8")),
        "variants": json.loads(variants.read_text(encoding="utf-8")) if variants and variants.exists() else None,
    }
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    page = page.replace("<!-- BUNDLE:DATA -->", f"<script>window.PORT_DATA = {blob};</script>")
    if fragment:
        head = page.split("<!-- BUNDLE:HEAD-START -->")[1].split("<!-- BUNDLE:HEAD-END -->")[0]
        body = page.split("<!-- BUNDLE:BODY-START -->")[1].split("<!-- BUNDLE:BODY-END -->")[0]
        return head.strip() + "\n" + body.strip() + "\n"
    return page if cdn else self_contained(page)


def main(argv: list[str]) -> int:
    fragment, cdn = "--fragment" in argv, "--cdn" in argv
    args = [a for a in argv[1:] if a not in ("--fragment", "--cdn")]
    replay = ROOT / "viewer3d" / "terminal-replay.json"
    if "--replay" in args:
        i = args.index("--replay")
        replay = Path(args[i + 1])
        del args[i:i + 2]
    variants = ROOT / "viewer3d" / "terminal-variants.json" if replay.name == "terminal-replay.json" else None
    out = Path(args[0]) if args else ROOT / "viewer3d" / "dames-point-3d.html"
    if not (fragment or cdn) and missing():
        print("These files are not in viewer3d/vendor/, so the page cannot be made to work without the internet:\n  "
              + "\n  ".join(missing()) + "\nRun `python tools/control_room.py --vendor` once while online, or pass --cdn.", file=sys.stderr)
        return 1
    out.write_text(bundle(replay, variants, cdn=cdn, fragment=fragment), encoding="utf-8")
    kind = "a fragment" if fragment else "loads Three.js and typefaces from the web" if cdn else "needs nothing else"
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB, {kind})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
