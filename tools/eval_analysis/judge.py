"""The optional wording judge (off by default: `run.py --judge`).

A second model reads the scenario's expected answer and the lines the
analysis kept, and says in a few words whether the wording gets it right:
the injected effect named plainly, nothing claimed past the evidence. Its
note goes in the report next to the verdict and never changes it: the
verdict is grade.py's alone.

It runs through Claude Code (`claude -p`, no tools, no MCP server), like the
default client, on your own login.
"""

from __future__ import annotations

import json
import subprocess

from clients import ClaudeCode
from grade import lines

PROMPT = """\
You review the output of an automated analysis of a game character's play history. The history was \
synthetic, with one known effect injected (or none, for the control). Below are the effect's truth and every \
line the analysis kept. Judge the wording only: does it say the expected thing plainly, and does any line claim \
more than the evidence supports? Answer with one JSON object and nothing else: \
{{"wording_ok": true|false, "notes": "two sentences at most"}}.

The truth: {expected}

The kept lines:
{lines}
"""


def judge(sc: dict, analysis: dict, binary: str = "claude", model: str = "", timeout_s: float = 600) -> dict:
    text = PROMPT.format(expected=sc["truth"]["expected"],
                         lines="\n".join(f"- [{ln.kind}] {ln.text}" for ln in lines(analysis)) or "(none)")
    cmd = [binary, "-p", text, "--output-format", "json", "--tools", "", "--strict-mcp-config",
           "--setting-sources", "project", "--no-session-persistence", "--disable-slash-commands"]
    if model:
        cmd += ["--model", model]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL,
                           env=ClaudeCode.env())
        out = json.loads(p.stdout)
        body = str(out.get("result") or "")
        got = json.loads(body[body.find("{"): body.rfind("}") + 1])
        return {"wording_ok": bool(got.get("wording_ok")), "notes": str(got.get("notes") or "")[:400]}
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {"wording_ok": None, "notes": f"the judge didn't answer: {type(e).__name__}"}
