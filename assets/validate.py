#!/usr/bin/env python3
"""Check SVG accessibility, the cool palette, and charts against recorded data."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import build
import charts

HERE = Path(__file__).resolve().parent
OLD_ACCENTS = {"#f6b26b", "#c06bd6", "#5fd38d", "#ffe7b0", "#fff1d2", "#ffd08a", "#ffb35c"}


def main():
    generated = {
        "logo.svg": build.logo, "mark.svg": build.mark_svg, "icon.svg": build.icon,
        "banner.svg": build.banner, "how-it-works.svg": build.diagram,
        "how-it-works-mobile.svg": lambda: build.diagram(build.DIAGRAM_MOBILE),
        "showcase-v1.svg": charts.showcase, "showcase-mobile-v1.svg": charts.showcase_mobile,
        "speed.svg": charts.speed, "speed-mobile.svg": charts.speed_mobile,
        "eval.svg": charts.eval_chart, "eval-mobile.svg": charts.eval_mobile,
        "memory.svg": charts.memory, "memory-mobile.svg": charts.memory_mobile,
        "sign-in.svg": charts.sign_in, "sign-in-mobile.svg": charts.sign_in_mobile,
    }
    failures = []
    files = sorted(HERE.glob("*.svg"))
    for path in files:
        source = path.read_text()
        try:
            root = ET.fromstring(source)
        except ET.ParseError as error:
            failures.append(f"{path.name}: invalid XML: {error}")
            continue
        ids = [node.attrib["id"] for node in root.iter() if "id" in node.attrib]
        if len(ids) != len(set(ids)):
            failures.append(f"{path.name}: duplicate IDs")
        for label in ("title", "desc"):
            node = root.find(f"{{http://www.w3.org/2000/svg}}{label}")
            if node is None or not (node.text or "").strip():
                failures.append(f"{path.name}: missing {label}")
        if root.get("role") != "img" or root.get("aria-labelledby") != "title desc":
            failures.append(f"{path.name}: missing accessible image labels")
        if OLD_ACCENTS.intersection(re.findall(r"#[0-9a-fA-F]{6}", source.lower())):
            failures.append(f"{path.name}: old warm/green accent")
        for target in re.findall(r"url\(#([^)]+)\)", source):
            if target not in ids:
                failures.append(f"{path.name}: missing paint target {target}")
        if "mobile" in path.stem and root.get("viewBox", "").split()[2:3] != ["600"]:
            failures.append(f"{path.name}: mobile figure must use the 600px artboard")
        if path.name in generated and source != generated[path.name]():
            failures.append(f"{path.name}: stale artwork; regenerate from assets/build.py or assets/charts.py")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"PASS: {len(files)} accessible SVGs; cool palette; {len(generated)} generated figures match sources and recorded JSON")


if __name__ == "__main__":
    main()
