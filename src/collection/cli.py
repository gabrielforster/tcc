"""Command line interface for the data pipeline."""

import typer
from rich.console import Console
from rich.table import Table

from collection.config import settings

app = typer.Typer(help="Multi-agent debt collection system pipeline.", no_args_is_help=True)
console = Console()


@app.command()
def extract(
    source: str = typer.Option(
        None,
        help="synthetic | home-credit | bank-marketing | erp (defaults to DATA_SOURCE in .env)",
    ),
) -> None:
    """Extract the raw dataset from the configured source into data/raw."""
    from collection.data.ingest import extract as _extract

    dataset = _extract(source)
    table = Table(title=f"Raw dataset ({source or settings.data_source})")
    table.add_column("table")
    table.add_column("rows", justify="right")
    table.add_column("covered", justify="center")
    for name, df in dataset.as_dict().items():
        covered = "[green]yes[/]" if name in dataset.provides else "[dim]-[/]"
        table.add_row(name, f"{len(df):,}", covered)
    console.print(table)
    console.print(f"[green]Written to[/] {settings.dir_raw}")


@app.command()
def ingest() -> None:
    """Anonymize (LGPD), validate the schema and write the dataset to data/interim."""
    from collection.data.ingest import ingest as _ingest

    for name, path in _ingest().items():
        console.print(f"[green]ok[/] {name} -> {path}")


@app.command()
def dictionary() -> None:
    """Generate docs/data-dictionary.md from the domain schema."""
    from collection.data.dictionary import generate

    console.print(f"[green]ok[/] {generate()}")


@app.command()
def eda() -> None:
    """Generate docs/eda.md and the charts under docs/eda/."""
    from collection.eda.report import generate_report

    console.print(f"[green]ok[/] {generate_report()}")


@app.command()
def features(
    task: str = typer.Option("both", help="propensity | default | both"),
) -> None:
    """Build features, the chronological 70/15/15 split and the preprocessor."""
    from collection.features.build import build_splits, save_splits
    from collection.features.preprocess import fit_and_save

    tasks = ["propensity", "default"] if task == "both" else [task]
    for t in tasks:
        try:
            splits = build_splits(t)
        except ValueError as error:
            # Expected when the ingested source carries no receivables; a traceback here
            # would suggest a bug rather than a source that covers a different table.
            console.print(f"[yellow]skipped[/] {error}")
            raise typer.Exit(code=1) from None
        save_splits(t)
        preprocessor_path = fit_and_save(splits)
        console.print(f"\n[bold]Task: {t}[/] (target `{splits.target}`)")
        console.print(splits.summary().to_string(index=False))
        console.print(f"[green]preprocessor[/] -> {preprocessor_path}")


@app.command()
def train(
    task: str = typer.Option("both", help="propensity | default | both"),
    tune: bool = typer.Option(True, help="run the randomised hyperparameter search"),
) -> None:
    """Train the candidate models, pick a champion and score it once on the test split."""
    import joblib

    from collection.models import report as model_report
    from collection.models.train import train_task

    tasks = ["propensity", "default"] if task == "both" else [task]
    for t in tasks:
        console.print(f"\n[bold]Training: {t}[/]")
        result = train_task(t, tune=tune)
        console.print(result.comparison_table().to_string(index=False))
        console.print(f"\n[green]champion[/] {result.champion} (threshold {result.threshold:.2f})")
        console.print(
            f"[green]test[/] average_precision={result.test.average_precision:.4f} "
            f"roc_auc={result.test.roc_auc:.4f} f1={result.test.f1:.4f} "
            f"(majority baseline average_precision={result.test_baseline['average_precision']:.4f})"
        )

        from collection.features.build import build_splits

        splits = build_splits(t)
        bundle = joblib.load(settings.dir_processed / f"model_{t}.joblib")
        x_test = splits.test.drop(columns=[splits.target])
        curve = model_report.curves(
            bundle["pipeline"], x_test, splits.test[splits.target], result.source, t
        )
        console.print(f"[green]report[/] -> {model_report.write(result, curve)}")


@app.command()
def explain(
    task: str = typer.Option("both", help="propensity | default | both"),
) -> None:
    """Permutation importance and SHAP values for the trained champion."""
    import joblib

    from collection.features.build import build_splits
    from collection.models.explain import explain as _explain
    from collection.models.explain import write_report

    tasks = ["propensity", "default"] if task == "both" else [task]
    for t in tasks:
        path = settings.dir_processed / f"model_{t}.joblib"
        if not path.exists():
            console.print(f"[yellow]skipped[/] no model for '{t}'. Run `collection train` first.")
            continue
        bundle = joblib.load(path)
        splits = build_splits(t)
        x_test = splits.test[bundle["features"]]
        y_test = splits.test[splits.target].to_numpy()
        console.print(f"\n[bold]Explaining: {t}[/] ({bundle['champion']})")
        explanation = _explain(bundle["pipeline"], x_test, y_test, t, bundle["source"])
        console.print(explanation.permutation.head(10).to_string(index=False))
        console.print(f"[green]report[/] -> {write_report(explanation)}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, help="reload on code changes"),
    seed_demo: bool = typer.Option(
        False, help="seed the proactive agent's contacts into this server's chat channel"
    ),
) -> None:
    """Serve the scoring API (POST /score), and the chat channel when it is enabled.

    With `--seed-demo` the server runs the outbound path at startup against the channel it
    is about to serve, so opening the widget as one of the fixture customers shows the
    scheduled message already waiting. The outcome is printed to the server log.
    """
    import os

    import uvicorn

    from collection.api.app import available_models

    if seed_demo:
        # uvicorn is started by import string (so `--reload` can re-import the app in a
        # child process), which means the flag cannot be passed as an argument and has to
        # travel in the environment, where a `--reload` child inherits it. Without
        # `--reload` the app is imported into *this* process, where `settings` was built
        # before the environment changed, so the live object is set too.
        os.environ["CHAT_SEED_DEMO"] = "1"
        settings.chat_seed_demo = True

    models = available_models()
    if not models:
        console.print("[yellow]warning[/] no trained models found; /score will return 404.")
    else:
        console.print(f"[green]models[/] {', '.join(models)}")
    if settings.chat_enabled:
        console.print(f"[green]chat[/] widget demo at http://{host}:{port}/chat/demo")
        console.print(f"[dim]origins[/] {', '.join(settings.chat_origins)}")
        if seed_demo:
            console.print(
                "[green]seed[/] scheduled contacts are seeded into this server's channel "
                "at startup; the demo page identifies itself as the seeded customer"
            )
    uvicorn.run("collection.api.app:app", host=host, port=port, reload=reload)


@app.command()
def demo() -> None:
    """Route a mix of events through the orchestrator and show what happened to each."""
    from collection.agents.demo import run

    result = run()
    trace = result.trace

    table = Table(title="Events processed, in the order the orchestrator handled them")
    table.add_column("#", justify="right")
    table.add_column("event")
    table.add_column("customer")
    table.add_column("priority")
    table.add_column("handled by")
    for i, event in enumerate(trace.processed, start=1):
        table.add_row(
            str(i),
            event.type.value,
            event.customer_id,
            event.priority.name.lower(),
            trace.handled_by[event.event_id],
        )
    console.print(table)

    for event in trace.suppressed:
        console.print(
            f"[yellow]suppressed[/] {event.type.value} for {event.customer_id} (opted out)"
        )
    for event in trace.produced:
        if event.type.value == "contact_scheduled":
            console.print(
                f"[green]scheduled[/] {event.customer_id} via {event.payload['channel']} "
                f"(stage {event.payload['delinquency_stage']}, attempt "
                f"{event.payload['attempt_number']}, expected value "
                f"R$ {event.payload['expected_value']})"
            )
        if event.type.value == "suppressed":
            console.print(
                f"[yellow]blocked[/] {event.customer_id}: {event.payload['reason']} — "
                f"{event.payload['detail']}"
            )
    for event in trace.unrouted:
        console.print(f"[dim]unrouted[/] {event.type.value} (no agent handles it yet)")
    console.print(f"\n[green]summary[/] {trace.counts()}")


@app.command()
def rag(
    query: str = typer.Option(None, help="run a single query against the index"),
    top_k: int = typer.Option(5),
    embedder: str = typer.Option("tfidf", help="tfidf | sentence-transformer"),
    evaluate_only: bool = typer.Option(False, "--evaluate", help="only run the evaluation"),
) -> None:
    """Build the knowledge index, evaluate retrieval, or answer a single query."""
    from collection.rag.documents import load_directory
    from collection.rag.embeddings import get_embedder
    from collection.rag.evaluate import load_questions, sweep
    from collection.rag.index import VectorIndex

    documents = load_directory(settings.dir_knowledge)
    index = VectorIndex(embedder=get_embedder(embedder))
    index.add_documents(documents).build()

    stats = index.stats()
    console.print(
        f"[green]indexed[/] {stats['documents']} documents -> {stats['chunks']} chunks "
        f"({stats['embedder']}, {stats['dimensions']} dims, "
        f"mean {stats['mean_words_per_chunk']} words/chunk)"
    )

    if query and not evaluate_only:
        for hit in index.search(query, top_k=top_k):
            preview = " ".join(hit.text.split())[:160]
            console.print(f"\n[bold]{hit.document_id}[/] ({hit.score:.3f})\n  {preview}...")
        return

    questions_file = settings.dir_knowledge / "questions.json"
    if not questions_file.exists():
        console.print("[yellow]no questions.json; skipping evaluation[/]")
        return
    questions = load_questions(questions_file)
    table = sweep(index, questions)
    console.print(f"\n[bold]Retrieval over {len(questions)} labelled questions[/]")
    console.print(table.to_string(index=False))

    index.save(settings.dir_processed / f"rag_index_{embedder}.joblib")


@app.command()
def answer(
    message: str = typer.Option(None, help="answer a single customer message"),
    responder: str = typer.Option("template", help="template | llm"),
    compare: bool = typer.Option(False, help="evaluate both responders and write the report"),
    conversations: bool = typer.Option(
        False, "--conversations", help="evaluate multi-turn context instead"
    ),
    rewriter: str = typer.Option("concat", help="none | concat | llm (with --conversations)"),
    embedder: str = typer.Option("tfidf", help="tfidf | sentence-transformer"),
) -> None:
    """Answer a message, or evaluate the responders against the labelled questions."""
    from collection.agents.responsive import ResponsiveAgent, get_responder
    from collection.llm import evaluate as responder_eval
    from collection.rag.documents import load_directory
    from collection.rag.embeddings import get_embedder
    from collection.rag.evaluate import load_questions
    from collection.rag.index import VectorIndex

    index = VectorIndex(embedder=get_embedder(embedder))
    index.add_documents(load_directory(settings.dir_knowledge)).build()

    if message:
        agent = ResponsiveAgent(index=index, responder=get_responder(responder))
        reply = agent.answer(message)
        console.print(
            f"[bold]intent[/] {reply.classification.intent.value} "
            f"(confidence {reply.classification.confidence:.2f}) "
            f"· [bold]{reply.latency_ms:.0f} ms[/]"
        )
        if reply.escalated:
            console.print(f"[yellow]escalated[/] {reply.escalation_reason}")
        console.print(f"\n{reply.answer.text}\n")
        for hit in reply.passages:
            console.print(f"[dim]retrieved[/] {hit.document_id} ({hit.score:.3f})")
        return

    if conversations:
        from collection.agents import conversation_eval

        dialogues_file = settings.dir_knowledge / "conversations.json"
        if not dialogues_file.exists():
            console.print("[yellow]no conversations.json; nothing to evaluate[/]")
            raise typer.Exit(code=1)
        dialogues = conversation_eval.load_dialogues(dialogues_file)
        arms = ["none", "concat", "llm"] if compare else ["none", rewriter]
        table, results = conversation_eval.compare(index, dialogues, arms, get_responder(responder))
        turns = sum(len(dialogue.turns) for dialogue in dialogues)
        console.print(
            f"\n[bold]Context strategies over {len(dialogues)} dialogues ({turns} turns)[/]"
        )
        console.print(table.to_string(index=False))
        sweep = conversation_eval.sweep_window(index, dialogues, responder=get_responder(responder))
        console.print("\n[bold]Context window[/]")
        console.print(sweep.to_string(index=False))
        path = conversation_eval.write_report(
            table,
            results,
            settings.dir_docs / "conversation-comparison.md",
            window_sweep=sweep,
        )
        console.print(f"[green]report[/] -> {path}")
        return

    questions_file = settings.dir_knowledge / "questions.json"
    if not questions_file.exists():
        console.print("[yellow]no questions.json; nothing to evaluate[/]")
        raise typer.Exit(code=1)
    questions = load_questions(questions_file)

    arms = ["template", "llm"] if compare else [responder]
    table, results = responder_eval.compare(index, questions, arms)
    console.print(f"\n[bold]Responders over {len(questions)} labelled questions[/]")
    console.print(table.to_string(index=False))
    for result in results:
        if not result.within_budget:
            console.print(
                f"[yellow]over budget[/] {result.responder} p95 {result.latency_p95:.0f} ms "
                f"> {responder_eval.LATENCY_BUDGET_MS:.0f} ms"
            )
        if result.fallbacks:
            console.print(
                f"[yellow]{result.fallbacks} fallback(s)[/] in {result.responder}: "
                "generation failed and the template answered instead."
            )
    path = responder_eval.write_report(
        table, results, settings.dir_docs / "responder-comparison.md"
    )
    console.print(f"[green]report[/] -> {path}")


@app.command()
def outreach(
    composer: str = typer.Option("template", help="template | llm"),
    compare: bool = typer.Option(False, help="evaluate both composers and write the report"),
    days_late: int = typer.Option(None, help="compose one message for a debt this many days late"),
    amount: float = typer.Option(1250.0, help="amount in BRL, with --days-late"),
    name: str = typer.Option("", help="customer name, with --days-late"),
    channel: str = typer.Option("chat", help="chat | voice | sms | email, with --days-late"),
) -> None:
    """Write one outbound message, or evaluate the composers against the labelled briefs."""
    from collection.outreach import evaluate as outreach_eval
    from collection.outreach.brief import Brief
    from collection.outreach.compliance import check
    from collection.outreach.compose import get_composer

    if days_late is not None:
        brief = Brief(
            customer_id="cli",
            customer_name=name,
            amount=amount,
            days_late=days_late,
            channel=channel,
        )
        message = get_composer(composer).compose(brief)
        result = check(message, brief)
        console.print(
            f"[bold]{message.stage}[/] via {message.channel} "
            f"· {result.length} chars · {message.composer}"
        )
        console.print(f"\n{message.text}\n")
        if result.compliant:
            console.print("[green]compliant[/]")
        else:
            console.print(f"[red]violations[/] {', '.join(result.violations)}")
        return

    briefs_file = settings.dir_knowledge / "outreach-briefs.json"
    if not briefs_file.exists():
        console.print("[yellow]no outreach-briefs.json; nothing to evaluate[/]")
        raise typer.Exit(code=1)
    briefs = outreach_eval.load(briefs_file)

    arms = ["template", "llm"] if compare else [composer]
    table, results = outreach_eval.compare(briefs, arms)
    console.print(f"\n[bold]Composers over {len(briefs)} labelled briefs[/]")
    console.print(table.to_string(index=False))
    for result in results:
        if result.compliance_rate < 1.0:
            offenders = [r for r in result.records if not r.compliance.compliant]
            console.print(
                f"[yellow]{len(offenders)} non-compliant[/] in {result.composer}: "
                + "; ".join(sorted({v for r in offenders for v in r.compliance.violations}))
            )
        if result.fallbacks:
            console.print(
                f"[yellow]{result.fallbacks} fallback(s)[/] in {result.composer}: "
                "generation failed and the template composed instead."
            )
    path = outreach_eval.write_report(table, results, settings.dir_docs / "outreach-comparison.md")
    console.print(f"[green]report[/] -> {path}")

    # The control arm is a hard gate, the LLM arm is not. Slot filling that cannot be
    # compliant on every labelled brief means the templates and the checker disagree, and
    # that has to stop a build; a model writing a bad message is a result to report.
    control = next((r for r in results if r.composer == "template"), None)
    if control is not None and control.compliance_rate < 1.0:
        console.print("[red]the template arm is not compliant on every brief[/]")
        raise typer.Exit(code=1)


@app.command()
def voice(
    audio: str = typer.Option(None, help="transcribe one clip and print what was heard"),
    transcriber: str = typer.Option("whisper", help="transcriber for --audio"),
    repeats: int = typer.Option(None, help="degraded variants per message per rate"),
) -> None:
    """Transcribe a voice note, or sweep what recognition error costs the pipeline."""
    from collection.voice import audio as audio_module
    from collection.voice import evaluate as voice_eval
    from collection.voice.transcribe import (
        MIN_CONFIDENCE,
        TranscriptionError,
        get_transcriber,
        needs_confirmation,
    )

    if audio is not None:
        try:
            clip = audio_module.accept(audio)
            result = get_transcriber(transcriber).transcribe(clip)
        except (audio_module.AudioError, TranscriptionError) as error:
            console.print(f"[red]{type(error).__name__}[/] {error}")
            raise typer.Exit(code=1) from error
        console.print(
            f"[bold]{clip.path.name}[/] · {clip.codec} · {clip.duration_s:.1f}s "
            f"· {result.transcriber} · confidence {result.confidence:.2f} "
            f"· {result.real_time_factor:.2f}x real time"
        )
        console.print(f"\n{result.text}\n")
        if not result.usable:
            console.print(f"[yellow]below {MIN_CONFIDENCE} confidence[/]: this would escalate")
        elif needs_confirmation(result.text):
            console.print("[yellow]carries a figure[/]: the agent would read it back first")
        return

    messages_file = settings.dir_knowledge / "voice-messages.json"
    if not messages_file.exists():
        console.print("[yellow]no voice-messages.json; nothing to evaluate[/]")
        raise typer.Exit(code=1)
    messages = voice_eval.load(messages_file)
    table, results = voice_eval.sweep(messages, repeats=repeats or voice_eval.REPEATS)
    console.print(f"\n[bold]Recognition error over {len(messages)} labelled voice messages[/]")
    console.print(table.to_string(index=False))
    path = voice_eval.write_report(table, results, settings.dir_docs / "voice-report.md")
    console.print(f"[green]report[/] -> {path}")

    # The control arm is a hard gate for the same reason it is everywhere else. At zero
    # corruption the transcript is the reference, so the first two conditions hold by
    # construction and the third is the one that bites: a labelled message claiming a
    # figure its own transcript does not contain as a whole token. That makes this an
    # integrity check on the labelled set, which is what every row below it rests on.
    control = results[0]
    if control.wer > 0 or control.intent_agreement < 1.0 or control.figure_survival < 1.0:
        console.print("[red]the control arm did not reproduce the reference transcripts[/]")
        raise typer.Exit(code=1)


@app.command()
def seed() -> None:
    """Report what the outbound path decides for each overdue receivable.

    It runs the whole path in this process -- proactive agent, rules engine, composition,
    compliance check, mailbox -- and prints the outcome. The mailbox it fills belongs to
    this command and dies with it, so this does **not** leave a message for a separate
    `collection serve` to deliver: a mailbox is an in-memory dict. The live demo is
    `collection serve --seed-demo`, where the server seeds the channel it is serving.

    Nothing here needs a network or an API key -- the template composer writes the
    message, which is also the arm that CI exercises.
    """
    from collection.channels.outbound import run as seed_contacts

    result = seed_contacts()

    table = Table(title="Scheduled contacts, and what the chat channel did with each")
    table.add_column("customer")
    table.add_column("outcome")
    table.add_column("detail")
    for event, delivery_id in result.seeded:
        table.add_row(event.customer_id, "[green]seeded[/]", delivery_id)
    for event in result.other_channel:
        table.add_row(
            event.customer_id, "[dim]other channel[/]", str(event.payload.get("channel", ""))
        )
    for event in result.suppressed:
        table.add_row(
            event.customer_id,
            "[yellow]suppressed[/]",
            str(event.payload.get("detail") or event.payload.get("reason") or "opted out"),
        )
    for event in result.escalated:
        table.add_row(event.customer_id, "[red]escalated[/]", str(event.payload.get("detail", "")))
    console.print(table)

    console.print(
        f"\n[green]summary[/] {len(result.seeded)} seeded · "
        f"{len(result.suppressed)} suppressed · {len(result.other_channel)} other channel · "
        f"{len(result.escalated)} escalated · {result.total} considered"
    )
    # Said plainly, because the table above is easy to read as "sent".
    console.print(
        "[dim]Seeded, not pushed: there is no push transport, so each message waits in "
        "the customer's mailbox until the widget is opened.[/]"
    )


@app.command()
def pipeline() -> None:
    """Run extract + ingest + dictionary + eda + features."""
    extract(source=None)
    ingest()
    dictionary()
    eda()
    features(task="both")


if __name__ == "__main__":
    app()
