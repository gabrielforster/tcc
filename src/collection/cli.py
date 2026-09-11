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
def pipeline() -> None:
    """Run extract + ingest + dictionary + eda + features."""
    extract(source=None)
    ingest()
    dictionary()
    eda()
    features(task="both")


if __name__ == "__main__":
    app()
