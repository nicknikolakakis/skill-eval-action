#!/usr/bin/env python3
"""Core eval pipeline: discover → execute → grade → aggregate.

Single script that runs the entire eval pipeline for CI.
Reads config from environment variables, writes results to WORKSPACE,
and sets GitHub Actions outputs.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

SKILL_NAME = os.environ["SKILL_NAME"]
SKILL_PATH = Path(os.environ["SKILL_PATH"])
WORKSPACE = Path(os.environ["WORKSPACE"])
EVAL_TIMEOUT = int(os.environ.get("EVAL_TIMEOUT", "120"))
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "80"))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_evals(skill_path: Path) -> list[dict]:
    """Read all .yaml eval files from the skill's evals/ directory."""
    evals_dir = skill_path / "evals"
    if not evals_dir.is_dir():
        return []
    cases = []
    for yaml_file in sorted(evals_dir.glob("*.yaml")):
        case = yaml.safe_load(yaml_file.read_text())
        case.setdefault("name", yaml_file.stem)
        case.setdefault("expect_skill", True)
        case.setdefault("timeout", EVAL_TIMEOUT)
        case["_source"] = str(yaml_file)
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def execute_case(case: dict, skill_content: str, case_dir: Path) -> dict:
    """Run a single eval case via claude -p and capture output."""
    case_dir.mkdir(parents=True, exist_ok=True)

    # Create temp dir with any specified files
    work_dir = Path(tempfile.mkdtemp(prefix=f"eval-{case['name']}-"))
    for file_spec in case.get("files", []):
        fp = work_dir / file_spec["path"]
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(file_spec.get("content", ""))

    # Inject skill content for positive trigger cases
    raw_prompt = case.get("prompt", "")
    if skill_content and case.get("expect_skill", True):
        prompt = (
            f"Follow these skill instructions when responding:\n\n"
            f"<skill-instructions>\n{skill_content}\n</skill-instructions>\n\n"
            f"User request: {raw_prompt}"
        )
    else:
        prompt = raw_prompt

    start = time.time()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"],
            capture_output=True, text=True,
            timeout=case.get("timeout", EVAL_TIMEOUT),
            cwd=str(work_dir), env=env,
        )
        elapsed = time.time() - start
        response_text = ""
        total_tokens = 0
        skill_triggered = False

        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                for content in event.get("message", {}).get("content", []):
                    if content.get("type") == "text":
                        response_text += content.get("text", "")
                    elif content.get("type") == "tool_use":
                        if content.get("name") == "Skill" and SKILL_NAME in json.dumps(content.get("input", {})):
                            skill_triggered = True
            elif event.get("type") == "result":
                total_tokens = event.get("total_tokens", 0)
                if not response_text:
                    response_text = event.get("result", "")

        # Write outputs
        (case_dir / "response.md").write_text(response_text)
        (case_dir / "timing.json").write_text(json.dumps({
            "total_tokens": total_tokens,
            "duration_seconds": round(elapsed, 1),
        }, indent=2))
        (case_dir / "eval_metadata.json").write_text(json.dumps({
            "prompt": raw_prompt,
            "criteria": case.get("criteria", []),
            "expect_skill": case.get("expect_skill", True),
            "skill_triggered": skill_triggered,
        }, indent=2))

        return {
            "name": case["name"], "status": "completed",
            "elapsed": round(elapsed, 1), "tokens": total_tokens,
            "skill_triggered": skill_triggered, "response": response_text,
        }
    except subprocess.TimeoutExpired:
        return {"name": case["name"], "status": "timeout", "elapsed": round(time.time() - start, 1), "tokens": 0, "response": ""}
    except Exception as e:
        return {"name": case["name"], "status": "error", "elapsed": 0, "tokens": 0, "response": "", "error": str(e)}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade_case(case: dict, exec_result: dict, case_dir: Path) -> dict:
    """Grade an executed eval case via claude -p."""
    criteria = case.get("criteria", [])
    criteria_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(criteria))
    response = exec_result.get("response", "(No response captured)")

    if len(response) > 10000:
        response = response[:10000] + "\n\n... (truncated at 10KB) ..."

    grader_prompt = f"""You are an eval grader. Grade this skill response against criteria. Be strict — FAIL if evidence is weak or superficial.

CRITERIA:
{criteria_text}

RESPONSE:
{response}

Output ONLY valid JSON in this exact format (no markdown, no explanation):
{{
  "expectations": [
    {{"text": "criterion text", "passed": true/false, "evidence": "specific quote or description"}}
  ],
  "summary": {{"passed": N, "failed": N, "total": N, "pass_rate": 0.0}}
}}"""

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        result = subprocess.run(
            ["claude", "-p", grader_prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        output = result.stdout.strip()
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        grading = json.loads(output)
        (case_dir / "grading.json").write_text(json.dumps(grading, indent=2) + "\n")
        return grading

    except Exception as e:
        fallback = {
            "expectations": [{"text": c, "passed": False, "evidence": f"Grading failed: {e}"} for c in criteria],
            "summary": {"passed": 0, "failed": len(criteria), "total": len(criteria), "pass_rate": 0.0},
        }
        (case_dir / "grading.json").write_text(json.dumps(fallback, indent=2) + "\n")
        return fallback


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    # Validate
    skill_md = SKILL_PATH / "SKILL.md"
    if not skill_md.exists():
        print(f"::error::SKILL.md not found at {skill_md}")
        sys.exit(1)

    cases = discover_evals(SKILL_PATH)
    if not cases:
        print(f"::error::No eval YAML files in {SKILL_PATH / 'evals'}")
        sys.exit(1)

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    skill_content = skill_md.read_text()

    print(f"Evaluating: {SKILL_NAME} ({len(cases)} cases)")

    # Execute
    exec_results = []
    for i, case in enumerate(cases):
        case_slug = case["name"].replace(" ", "-").lower()
        case_dir = WORKSPACE / case_slug
        print(f"::group::Execute [{i+1}/{len(cases)}]: {case['name']}")
        er = execute_case(case, skill_content, case_dir)
        exec_results.append(er)
        print(f"Status: {er['status']} | Time: {er['elapsed']}s | Tokens: {er['tokens']}")
        print("::endgroup::")

    # Grade
    gradings = []
    for i, (case, er) in enumerate(zip(cases, exec_results)):
        case_slug = case["name"].replace(" ", "-").lower()
        case_dir = WORKSPACE / case_slug
        print(f"::group::Grade [{i+1}/{len(cases)}]: {case['name']}")

        if er["status"] != "completed":
            fallback = {
                "expectations": [{"text": c, "passed": False, "evidence": f"Execution {er['status']}"} for c in case.get("criteria", [])],
                "summary": {"passed": 0, "failed": len(case.get("criteria", [])), "total": len(case.get("criteria", [])), "pass_rate": 0.0},
            }
            (case_dir / "grading.json").write_text(json.dumps(fallback, indent=2) + "\n")
            gradings.append(fallback)
            print(f"Skipped (execution {er['status']})")
        else:
            gr = grade_case(case, er, case_dir)
            gradings.append(gr)
            s = gr.get("summary", {})
            print(f"Result: {s.get('passed', 0)}/{s.get('total', 0)} passed")
        print("::endgroup::")

    # Aggregate
    total_passed = sum(g.get("summary", {}).get("passed", 0) for g in gradings)
    total_criteria = sum(g.get("summary", {}).get("total", 0) for g in gradings)
    total_time = sum(r.get("elapsed", 0) for r in exec_results)
    total_tokens = sum(r.get("tokens", 0) for r in exec_results)
    pass_rate = (total_passed / total_criteria * 100) if total_criteria > 0 else 0

    # Write summary
    summary = {
        "skill_name": SKILL_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(cases),
        "total_passed": total_passed,
        "total_criteria": total_criteria,
        "pass_rate": round(pass_rate, 1),
        "total_time": round(total_time, 1),
        "total_tokens": total_tokens,
        "results": [
            {
                "name": case["name"],
                "status": er["status"],
                "elapsed": er["elapsed"],
                "tokens": er["tokens"],
                "criteria_passed": gr.get("summary", {}).get("passed", 0),
                "criteria_total": gr.get("summary", {}).get("total", 0),
            }
            for case, er, gr in zip(cases, exec_results, gradings)
        ],
    }
    (WORKSPACE / "summary.json").write_text(json.dumps(summary, indent=2))

    # Set GitHub Actions outputs
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"pass_rate={pass_rate:.1f}\n")
            f.write(f"passed={total_passed}\n")
            f.write(f"total={total_criteria}\n")
            f.write(f"cases_run={len(cases)}\n")

    # Write step summary
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a") as f:
            status_emoji = "✅" if pass_rate >= PASS_THRESHOLD else "❌"
            f.write(f"## {status_emoji} Skill Eval: {SKILL_NAME}\n\n")
            f.write(f"**Pass rate: {total_passed}/{total_criteria} ({pass_rate:.1f}%)** | ")
            f.write(f"Threshold: {PASS_THRESHOLD:.0f}% | Time: {total_time:.1f}s | Tokens: {total_tokens:,}\n\n")
            f.write("| # | Case | Status | Criteria | Time | Tokens |\n")
            f.write("|---|------|--------|----------|------|--------|\n")
            for i, r in enumerate(summary["results"]):
                s = "PASS" if r["criteria_passed"] == r["criteria_total"] and r["status"] == "completed" else "FAIL"
                f.write(f"| {i+1} | {r['name']} | {s} | {r['criteria_passed']}/{r['criteria_total']} | {r['elapsed']}s | {r['tokens']:,} |\n")
            f.write("\n")

    # Print results table
    print(f"\n{'='*70}")
    print(f"Skill: {SKILL_NAME} | Pass rate: {total_passed}/{total_criteria} ({pass_rate:.1f}%)")
    print(f"{'='*70}")
    for i, r in enumerate(summary["results"]):
        s = "PASS" if r["criteria_passed"] == r["criteria_total"] and r["status"] == "completed" else "FAIL"
        print(f"  {i+1}. [{s}] {r['name']} — {r['criteria_passed']}/{r['criteria_total']} ({r['elapsed']}s, {r['tokens']:,} tokens)")
    print(f"\nTotal: {total_time:.1f}s | {total_tokens:,} tokens")


if __name__ == "__main__":
    main()
