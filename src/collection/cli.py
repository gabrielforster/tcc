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
) -> None:
    """Serve the scoring API (POST /score)."""
    import uvicorn

    from collection.api.app import available_models

    models = available_models()
    if not models:
        console.print("[yellow]warning[/] no trained models found; /score will return 404.")
    else:
        console.print(f"[green]models[/] {', '.join(models)}")
    uvicorn.run("collection.api.app:app", host=host, port=port, reload=reload)


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
