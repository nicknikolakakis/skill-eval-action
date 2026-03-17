#!/usr/bin/env python3
"""Generate a self-contained HTML eval viewer from workspace data.

Embeds all eval results as JSON into a single HTML file that can be
viewed offline or uploaded as a GitHub Actions artifact.
"""

import json
import os
from pathlib import Path

WORKSPACE = Path(os.environ["WORKSPACE"])
SKILL_NAME = os.environ["SKILL_NAME"]
TEMPLATE_PATH = Path(os.environ.get("TEMPLATE_PATH", ""))


def build_viewer_data() -> dict:
    """Build the data payload for the viewer template."""
    summary_path = WORKSPACE / "summary.json"
    if not summary_path.exists():
        return {"skill_name": SKILL_NAME, "runs": [], "benchmark": None}

    summary = json.loads(summary_path.read_text())

    # Load grading details for each case
    cases = []
    for r in summary.get("results", []):
        case_slug = r["name"].replace(" ", "-").lower()
        case_dir = WORKSPACE / case_slug

        case_data = {"name": r["name"], "status": r["status"]}

        grading_path = case_dir / "grading.json"
        if grading_path.exists():
            case_data["grading"] = json.loads(grading_path.read_text())

        response_path = case_dir / "response.md"
        if response_path.exists():
            case_data["response"] = response_path.read_text()[:5000]

        metadata_path = case_dir / "eval_metadata.json"
        if metadata_path.exists():
            case_data["metadata"] = json.loads(metadata_path.read_text())

        timing_path = case_dir / "timing.json"
        if timing_path.exists():
            case_data["timing"] = json.loads(timing_path.read_text())

        cases.append(case_data)

    return {
        "skill_name": SKILL_NAME,
        "summary": summary,
        "cases": cases,
        "generated_at": summary.get("timestamp", ""),
    }


def main() -> None:
    if not TEMPLATE_PATH.exists():
        print(f"Warning: viewer template not found at {TEMPLATE_PATH}")
        # Generate a minimal self-contained viewer
        data = build_viewer_data()
        html = f"""<!DOCTYPE html>
<html><head><title>Eval: {SKILL_NAME}</title>
<style>body{{font-family:monospace;max-width:900px;margin:40px auto;padding:0 20px}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:8px;text-align:left}}
.pass{{color:green}}.fail{{color:red}}pre{{background:#f4f4f4;padding:12px;overflow-x:auto}}</style></head>
<body><h1>Skill Eval: {SKILL_NAME}</h1>
<pre id="data">{json.dumps(data, indent=2)}</pre>
</body></html>"""
        (WORKSPACE / "viewer.html").write_text(html)
        return

    # Use the full viewer template
    template = TEMPLATE_PATH.read_text()
    data = build_viewer_data()
    data_json = json.dumps(data)

    # Embed data into template
    if "/*__EMBEDDED_DATA__*/" in template:
        html = template.replace("/*__EMBEDDED_DATA__*/", f"window.__EVAL_DATA__ = {data_json};")
    else:
        # Fallback: inject before </head>
        script = f"<script>window.__EVAL_DATA__ = {data_json};</script>"
        html = template.replace("</head>", f"{script}\n</head>")

    output_path = WORKSPACE / "viewer.html"
    output_path.write_text(html)
    print(f"Viewer generated: {output_path}")

    # Set output
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"viewer_html={output_path}\n")


if __name__ == "__main__":
    main()
