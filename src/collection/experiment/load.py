"""Hold many conversations at once against the served chat, and measure what that costs.

The schedule's operational target is "≥ 100 concurrent interactions". This puts N customers
on the chat channel at the same instant -- each opens a session and then holds a short
conversation, one message after another, the way a person types -- and records every
request's latency and status.

Two choices make the number mean something:

* **The server is a separate process.** A client and a server sharing one interpreter
  share its lock, and the latency measured would be partly the client's. `serve` is the
  child's entry point; it builds the same app `collection serve` does.
* **The chat runs on the in-memory store with the template responder.** That is the path
  a fresh clone serves, so the measurement needs no database and no API key -- and it is
  stated as such in the report, because an LLM call per turn would add its own latency on
  top and nothing here measures it.

Only the standard library is used on the client side, so the check runs wherever the
package does.
"""

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from collection.config import settings

START = "<!-- concurrency:start -->"
END = "<!-- concurrency:end -->"
DEFAULT_LEVELS = (1, 50, 100, 200, 400)
DEFAULT_TURNS = 5
#: The responsive agent's latency target (schedule, meeting 11).
TARGET_SECONDS = 5.0


def serve(port: int) -> None:
    """Child-process entry point: the chat, in memory, with the template responder."""
    import uvicorn

    from collection.agents.responsive import TemplateResponder
    from collection.api.app import ModelRegistry, create_app
    from collection.api.chat import ChatService
    from collection.channels.web import ChatChannel
    from collection.rag.documents import load_directory
    from collection.rag.index import VectorIndex

    index = VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()
    channel = ChatChannel(retriever=index, responder=TemplateResponder())
    app = create_app(registry=ModelRegistry(loader=lambda: {}), chat=ChatService(channel=channel))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class Sample:
    kind: str  # "open" or "message"
    seconds: float
    status: int  # HTTP status, or 0 for a transport error


@dataclass
class Level:
    conversations: int
    turns: int
    wall_seconds: float
    samples: list[Sample] = field(default_factory=list)

    def latencies(self, kind: str) -> np.ndarray:
        return np.array([s.seconds for s in self.samples if s.kind == kind and s.status < 300])

    @property
    def errors(self) -> dict[int, int]:
        found: dict[int, int] = {}
        for sample in self.samples:
            if sample.status >= 300 or sample.status == 0:
                found[sample.status] = found.get(sample.status, 0) + 1
        return found

    @property
    def requests(self) -> int:
        return len(self.samples)


def _post(url: str, body: dict, timeout: float) -> tuple[int, dict | None, float]:
    data = json.dumps(body).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read() or b"null")
            return response.status, payload, time.perf_counter() - started
    except urllib.error.HTTPError as error:
        return error.code, None, time.perf_counter() - started
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return 0, None, time.perf_counter() - started


def _conversation(
    base: str, texts: list[str], barrier: threading.Barrier, out: list[Sample], timeout: float
) -> None:
    barrier.wait()
    status, payload, seconds = _post(f"{base}/chat/sessions", {}, timeout)
    out.append(Sample("open", seconds, status))
    if status != 201 or not payload:
        return
    session_id = payload["session_id"]
    for text in texts:
        status, _, seconds = _post(
            f"{base}/chat/sessions/{session_id}/messages", {"text": text}, timeout
        )
        out.append(Sample("message", seconds, status))


def run_level(
    base: str, conversations: int, turns: int, messages: list[str], timeout: float
) -> Level:
    barrier = threading.Barrier(conversations)
    out: list[Sample] = []
    threads = []
    for n in range(conversations):
        # Each customer says something different; deterministic, so a rerun is the same load.
        texts = [messages[(n * turns + t) % len(messages)] for t in range(turns)]
        thread = threading.Thread(
            target=_conversation, args=(base, texts, barrier, out, timeout), daemon=True
        )
        threads.append(thread)
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return Level(conversations, turns, time.perf_counter() - started, out)


def _wait_ready(base: str, process: subprocess.Popen, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"the chat server exited with {process.returncode}")
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.5)
    raise RuntimeError("the chat server did not become ready")


def run_load(
    levels: tuple[int, ...] = DEFAULT_LEVELS,
    turns: int = DEFAULT_TURNS,
    timeout: float = 60.0,
) -> list[Level]:
    messages = [
        m["text"] for m in json.loads((settings.dir_knowledge / "messages.json").read_text())
    ]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-c", f"from collection.experiment.load import serve; serve({port})"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_ready(base, process)
        # One untimed conversation first, so no level pays for a cold import.
        run_level(base, 1, 1, messages, timeout)
        return [run_level(base, n, turns, messages, timeout) for n in levels]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def _ms(values: np.ndarray, q: float) -> str:
    return f"{np.percentile(values, q) * 1000:.0f}" if len(values) else "—"


def table(results: list[Level]) -> list[str]:
    lines = [
        "| concurrent conversations | requests | errors | message p50 (ms) | p95 | p99 | max "
        "| session open p95 (ms) | wall (s) | requests/s | messages over 5 s |",
        "|--:|--:|:--|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for level in results:
        messages = level.latencies("message")
        opens = level.latencies("open")
        errors = ", ".join(f"{k or 'transport'}×{v}" for k, v in sorted(level.errors.items()))
        over = int((messages > TARGET_SECONDS).sum())
        lines.append(
            f"| {level.conversations} | {level.requests} | {errors or '0'} "
            f"| {_ms(messages, 50)} | {_ms(messages, 95)} | {_ms(messages, 99)} "
            f"| {_ms(messages, 100)} | {_ms(opens, 95)} | {level.wall_seconds:.1f} "
            f"| {level.requests / level.wall_seconds:.0f} | {over} |"
        )
    return lines


def write(results: list[Level], path: Path) -> Path:
    from datetime import date

    turns = results[0].turns if results else DEFAULT_TURNS
    block = "\n".join(
        [
            START,
            "",
            f"Generated by `make concurrency` on {date.today():%Y-%m-%d}. Each conversation "
            f"opens a session and sends {turns} messages in sequence; all conversations at a "
            "level start together behind a barrier. Server: one uvicorn process, in-memory "
            "store, TF-IDF retrieval, template responder (no LLM call in the turn).",
            "",
            *table(results),
            "",
            END,
        ]
    )
    if path.exists() and START in (text := path.read_text(encoding="utf-8")) and END in text:
        before, rest = text.split(START, 1)
        content = before + block + rest.split(END, 1)[1]
    elif path.exists():
        content = (
            path.read_text(encoding="utf-8").rstrip() + "\n\n## Concurrency\n\n" + block + "\n"
        )
    else:
        content = "# Controlled experiment: results\n\n## Concurrency\n\n" + block + "\n"
    path.write_text(content, encoding="utf-8")
    return path
