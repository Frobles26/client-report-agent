"""The agent loop: Claude plans the report and calls the tools in tools.py.

Each step: send the conversation to Claude -> Claude answers with text and/or tool calls ->
run the tools -> send the results back. It stops when Claude stops asking for tools, or
after MAX_STEPS. Every step is written to output/run_log.json so a reviewer can see
exactly what the agent did, and the run is recorded in ops.runs.

Run: python -m report_agent.agent.loop "Build the Q3 2026 client report for CAN_EQ"
"""

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from report_agent.agent.tools import TOOLS, ReportSession, ToolError
from report_agent.config import ANTHROPIC_MODEL

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"
MODEL = ANTHROPIC_MODEL
MAX_STEPS = 15

SYSTEM = """You prepare quarterly client reports for an investment firm.

Work in this order:
1. get_data_status: find the strategy and the latest date with data.
2. Turn the request into exact dates (a quarter runs from its first to its last calendar day).
   If the period ends after the latest date with data, stop and explain; do not build files.
3. get_report_numbers for that strategy and period.
4. build_excel.
5. write_commentary, then build_deck.
6. Reply with a two-sentence summary: what you built and the headline result.

Rules:
- Never calculate, estimate or round a number yourself. Use the figures exactly as
  get_report_numbers formats them. Copy decimals as given. Keep the sign, except after
  a word that already gives the direction ("trailed by 1.22%", "fell 2.00%").
- Commentary: 3 short paragraphs, 100 to 180 words, plain language for a client.
  Paragraph 1: the period return against the benchmark. Paragraph 2: the largest positive
  and negative contributors from the top 10, and the biggest sector. Paragraph 3: one or
  two risk figures from the past year, explained simply.
- No forecasts, no recommendations, no market news: only what the numbers show.
- If a tool returns an error, read it, fix the cause and try again.
- If the request is unclear (no strategy can be matched), stop and ask."""


@dataclass
class AgentResult:
    status: str                       # completed / step_limit / error
    final_text: str
    files: dict[str, str]
    steps: list[dict] = field(default_factory=list)
    log_path: Path | None = None


def _block_to_dict(block) -> dict:
    """Turn an SDK content block (or a test double) into the plain dict the API accepts."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    # Claude's reasoning before it acts. The API requires it to be sent back unchanged
    # (with its signature) while the agent is calling tools.
    if block.type == "thinking":
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    if block.type == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.data}
    if hasattr(block, "model_dump"):                 # any newer block type: pass it back as is
        return block.model_dump(exclude_none=True)
    raise ValueError(f"Unexpected content block type {block.type}")


def _run_tool(session: ReportSession, name: str, args: dict) -> tuple[str, bool]:
    """Run one tool. Returns (JSON result, is_error)."""
    try:
        return json.dumps(session.call(name, args), default=str), False
    except ToolError as exc:
        return json.dumps({"error": str(exc)}), True
    except Exception as exc:  # noqa: BLE001 - a tool bug goes back to the model, not a crash
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}), True


def run_agent(request: str, client, session: ReportSession | None = None,
              model: str = MODEL, max_steps: int = MAX_STEPS,
              log_path: Path | None = None, echo=None) -> AgentResult:
    """Run the agent until it finishes or hits max_steps. `client` is an anthropic.Anthropic."""
    session = session or ReportSession()
    log_path = log_path or OUTPUT_DIR / "run_log.json"
    echo = echo or (lambda _msg: None)
    messages: list[dict] = [{"role": "user", "content": request}]
    steps: list[dict] = []
    started = datetime.now()
    status, final_text = "step_limit", ""
    usage = {"input_tokens": 0, "output_tokens": 0}

    for step in range(1, max_steps + 1):
        response = client.messages.create(model=model, max_tokens=2048, system=SYSTEM,
                                          tools=TOOLS, messages=messages)
        content = [_block_to_dict(b) for b in response.content]
        messages.append({"role": "assistant", "content": content})
        for key in usage:
            usage[key] += getattr(response.usage, key, 0) or 0

        text = "\n".join(b["text"] for b in content if b["type"] == "text").strip()
        calls = [b for b in content if b["type"] == "tool_use"]
        thinking = "\n".join(b["thinking"] for b in content if b["type"] == "thinking").strip()
        record = {"step": step, "text": text, "thinking": thinking[:3000], "tool_calls": []}
        steps.append(record)

        if response.stop_reason != "tool_use" or not calls:
            status, final_text = "completed", text
            break

        results = []
        for call in calls:
            output, is_error = _run_tool(session, call["name"], call["input"])
            echo(f"  step {step}: {call['name']}" + ("  -> error" if is_error else ""))
            record["tool_calls"].append({"name": call["name"], "input": call["input"],
                                         "is_error": is_error, "output": output[:20000]})
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": output, "is_error": is_error})
        messages.append({"role": "user", "content": results})

    log = {
        "request": request, "model": model, "status": status,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().isoformat(timespec="seconds"),
        "steps_used": len(steps), "max_steps": max_steps, "usage": usage,
        "files": session.files, "commentary": session.commentary,
        "final_text": final_text, "steps": steps,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    return AgentResult(status, final_text, session.files, steps, log_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the agent to build a client report.")
    parser.add_argument("request", help='For example: "Build the Q3 2026 report for CAN_EQ"')
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    args = parser.parse_args()

    from anthropic import Anthropic

    from report_agent.config import ANTHROPIC_API_KEY
    from report_agent.db import get_engine
    from report_agent.etl.load import log_run

    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY is empty. Add it to your .env file.")
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    print(f"Agent ({MODEL}) working on: {args.request}")
    try:
        with log_run(get_engine(), "agent_report") as info:   # recorded in ops.runs
            result = run_agent(args.request, client, max_steps=args.max_steps, echo=print)
            info["rows_loaded"] = len(result.steps)         # here: steps the agent used
            info["message"] = result.final_text[:1000]
            if result.status != "completed":
                raise RuntimeError(f"Agent stopped: {result.status}. See {result.log_path}")
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"\n{result.final_text}\n")
    for kind, path in result.files.items():
        print(f"{kind}: {path}")
    print(f"log: {result.log_path}")


if __name__ == "__main__":
    main()
