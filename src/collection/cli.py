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
def pipeline() -> None:
    """Run extract + ingest + dictionary + eda + features."""
    extract(source=None)
    ingest()
    dictionary()
    eda()
    features(task="both")


if __name__ == "__main__":
    app()
