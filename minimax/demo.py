"""Run the ticket-filter SDD workflow from separate CLI processes."""

import argparse
import asyncio
import difflib
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from importlib.metadata import version
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TASK = "ticket-priority"
COLLECTION = "sdd_project_material"
MATERIALS = {
    "spec": "specs/priority.md",
    "code": "baseline/tickets.py",
    "tests": "tests/test_tickets.py",
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collection(run, *, create=False):
    import pyseekdb

    client = pyseekdb.Client(path=str(run / "seekdb"))
    if create:
        return client.get_or_create_collection(COLLECTION, embedding_function=None)
    return client.get_collection(COLLECTION, embedding_function=None)


def import_material(run, specification):
    paths = {**MATERIALS, "spec": specification}
    records = [
        {"kind": kind, "source": path, "text": (ROOT / path).read_text(encoding="utf-8")}
        for kind, path in paths.items()
    ]
    collection(run, create=True).upsert(
        ids=[f"{TASK}:{row['kind']}" for row in records],
        documents=[json.dumps(row, ensure_ascii=False) for row in records],
        embeddings=[[1.0] + [0.0] * 383 for _ in records],
    )


def context(run):
    ids = [f"{TASK}:{kind}" for kind in MATERIALS]
    result = collection(run).get(ids=ids)
    documents = dict(zip(result["ids"], result["documents"], strict=True))
    if set(documents) != set(ids):
        raise SystemExit("Run import before retrieving task context.")
    bundle = {
        "task": TASK,
        "objective": "Add priority filtering to list_tickets while preserving its default behavior.",
        "retrieval": {"engine": "seekdb", "client": version("pyseekdb"), "collection": COLLECTION, "ids": ids},
        "materials": [json.loads(documents[key]) for key in ids],
        "task_state": asyncio.run(task_state(run)),
    }
    write_json(run / "context.json", bundle)
    spec_name = Path(bundle["materials"][0]["source"]).stem
    write_json(run / f"context-{spec_name}.json", bundle)
    return bundle


async def task_state(run, event=None):
    from powercontext.builtin.artifacts.memory import MemoryEntryInput
    from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, open_builtin_runtime
    from powercontext.builtin.scope import ScopeDraft
    from powercontext.builtin.sources import ContentSource

    class LiteralPipeline:
        async def extract(self, request, /):
            return tuple(
                MemoryEntryInput(kind="document", text=source.content, sources=(source,))
                for source in request.sources
                if isinstance(source, ContentSource)
            )

    config = BuiltinConfig(database={"url": f"sqlite+aiosqlite:///{run / 'powercontext.db'}"})
    async with open_builtin_runtime(config, candidate_pipeline=LiteralPipeline()) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(title="Ticket priority", summary="MiniMax SDD task progress", idempotency_key=TASK)
        )
        memory = runtime.memory.for_scope(scope.scope_id)
        if event:
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id=f"{TASK}-{event['stage']}", content=json.dumps(event), metadata={"task": TASK}
                )
            )
            while (await memory.flush()).processed:
                pass
        entries = (await memory.list()).entries
        states = [json.loads(record.entry.text) for record in entries]
        return max(states, key=lambda value: value["stage"], default=None)


def test_version(run, source, name):
    command = [sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")]
    env = {**os.environ, "PYTHONPATH": str(source.parent)}
    env.pop("MINIMAX_API_KEY", None)
    process = subprocess.run(
        command, env=env, capture_output=True, text=True
    )
    result = {
        "command": command,
        "source": str(source.relative_to(ROOT) if source.is_relative_to(ROOT) else source.relative_to(run)),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "tests_sha256": hashlib.sha256((ROOT / MATERIALS["tests"]).read_bytes()).hexdigest(),
        "exit_code": process.returncode,
        "output": process.stdout + process.stderr,
    }
    write_json(run / f"{name}.json", result)
    print(result["output"], end="")
    return result


def prepare(run):
    if asyncio.run(task_state(run)):
        raise SystemExit("This run already has task progress. Use resume or a fresh --run-dir.")
    bundle = context(run)
    baseline = ROOT / MATERIALS["code"]
    result = test_version(run, baseline, "baseline-tests")
    if result["exit_code"] != 1 or "2 failed, 1 passed" not in result["output"]:
        raise SystemExit("Expected the two missing-feature assertions to fail and the regression to pass.")
    return bundle, baseline, result


def implement(run):
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        raise SystemExit("Set MINIMAX_API_KEY in this terminal.")
    bundle, baseline, result = prepare(run)
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3.1-Flash")
    url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.cn/v1").rstrip("/")
    prompt = (
        "Implement the specification using the retrieved source and tests. Return the complete "
        "replacement Python source for tickets.py only, without Markdown. Preserve the public signature.\n"
        + json.dumps(bundle, ensure_ascii=False)
    )
    request = urllib.request.Request(
        url + "/chat/completions",
        data=json.dumps(
            {"model": model, "messages": [{"role": "user", "content": prompt}], "max_completion_tokens": 4096}
        ).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        hint = " Check MINIMAX_BASE_URL and MINIMAX_MODEL." if error.code == 404 else ""
        raise SystemExit(f"MiniMax returned HTTP {error.code} for model {model}.{hint}") from None
    if not payload.get("choices"):
        status = payload.get("base_resp", {}).get("status_code", "unknown")
        raise SystemExit(f"MiniMax returned no completion (status {status}).")
    content = payload["choices"][0]["message"]["content"].strip()
    if "</think>" in content:
        content = content.split("</think>", 1)[1].strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1][:-3].strip()
    compile(content, "tickets.py", "exec")
    output = run / "generated" / "tickets.py"
    output.parent.mkdir(exist_ok=True)
    output.write_text(content + "\n", encoding="utf-8")
    write_json(run / "minimax.json", {"model": model, "response_id": payload.get("id"), "prompt": prompt, "code": content})
    diff = difflib.unified_diff(
        baseline.read_text(encoding="utf-8").splitlines(keepends=True),
        output.read_text(encoding="utf-8").splitlines(keepends=True),
        fromfile="baseline/tickets.py",
        tofile="generated/tickets.py",
    )
    (run / "tickets.diff").write_text("".join(diff), encoding="utf-8")
    save_progress(run, result, "Generated MiniMax patch")
    print("MiniMax generated tickets.diff. Review the generated code, then run resume.")


def replay(run):
    bundle, _, result = prepare(run)
    evidence = ROOT / "evidence"
    recorded = json.loads((evidence / "context-priority.json").read_text(encoding="utf-8"))
    if bundle["materials"] != recorded["materials"]:
        raise SystemExit("The recorded patch requires the original specification and materials. Run import.")
    source = (evidence / "generated/tickets.py").read_bytes()
    output = run / "generated/tickets.py"
    output.parent.mkdir(exist_ok=True)
    output.write_bytes(source)
    save_progress(run, result, "Loaded recorded MiniMax patch")
    print("Loaded the submitted MiniMax output. Run resume in a new process.")


def save_progress(run, result, patch_step):
    saved = asyncio.run(task_state(run, {
        "stage": 1,
        "next": "verify",
        "source": "generated/tickets.py",
        "completed": ["Recorded baseline behavior", patch_step],
        "baseline_test_exit_code": result["exit_code"],
    }))
    if not saved or saved["next"] != "verify":
        raise SystemExit("Could not read the saved PowerContext task state.")
    write_json(run / "state-saved.json", saved)


def resume(run):
    saved = asyncio.run(task_state(run))
    if not saved:
        raise SystemExit("No saved task. Run implement first.")
    print(json.dumps({"restored_from": "PowerContext", "state": saved}, ensure_ascii=False))
    write_json(run / f"state-restored-{saved['stage']}.json", saved)
    if saved["next"] == "verify":
        result = test_version(run, run / saved["source"], "final-tests")
        if result["exit_code"]:
            raise SystemExit(result["exit_code"])
        completed = asyncio.run(task_state(run, {
            **saved,
            "stage": 2,
            "next": "done",
            "completed": [*saved["completed"], "Passed feature and regression tests"],
            "test_exit_code": 0,
        }))
        write_json(run / "state-completed.json", completed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("import", "context", "implement", "replay", "resume"))
    parser.add_argument("--run-dir", type=Path, default=Path.home() / ".local/state/powercontext-minimax")
    parser.add_argument("--spec", choices=("specs/priority.md", "specs/priority-v2.md"), default="specs/priority.md")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=True)
    if args.action == "import":
        import_material(run, args.spec)
    elif args.action == "context":
        print(json.dumps(context(run), ensure_ascii=False, indent=2))
    elif args.action == "implement":
        implement(run)
    elif args.action == "replay":
        replay(run)
    else:
        resume(run)


if __name__ == "__main__":
    main()
