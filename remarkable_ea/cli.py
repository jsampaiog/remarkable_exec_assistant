"""Click CLI entrypoint.

Each subcommand loads the config lazily and delegates to the matching
component module so that running ``remarkable-ea --help`` never requires any
runtime secrets to be present.
"""

from __future__ import annotations

from pathlib import Path

import click

DEFAULT_CONFIG_PATH = "~/.remarkable-ea/config.yaml"


@click.group()
@click.option(
    "--config",
    "config_path",
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    envvar="REMARKABLE_EA_CONFIG",
    help="Path to config.yaml.",
)
@click.pass_context
def cli(ctx: click.Context, config_path: str) -> None:
    """reMarkable Executive Assistant."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = Path(config_path).expanduser()


def _load(ctx: click.Context):
    from remarkable_ea.config import load_config

    return load_config(ctx.obj["config_path"])


@cli.command()
@click.pass_context
def sync(ctx: click.Context) -> None:
    """Pull new or modified pages from whitelisted notebooks."""
    from remarkable_ea import sync as sync_mod

    sync_mod.run(_load(ctx))


@cli.command()
@click.pass_context
def extract(ctx: click.Context) -> None:
    """Extract structured JSON from newly synced pages."""
    from remarkable_ea import extract as extract_mod

    extract_mod.run(_load(ctx))


@cli.command()
@click.pass_context
def digest(ctx: click.Context) -> None:
    """Generate and send the daily digest email."""
    from remarkable_ea import digest as digest_mod

    digest_mod.run(_load(ctx))


@cli.command("ingest-replies")
@click.pass_context
def ingest_replies(ctx: click.Context) -> None:
    """Poll the inbox for replies to yesterday's digest and apply commands."""
    from remarkable_ea import ingest_replies as ingest_mod

    ingest_mod.run(_load(ctx))


@cli.command("run-daily")
@click.pass_context
def run_daily(ctx: click.Context) -> None:
    """Run the full daily pipeline: ingest-replies → sync → extract → digest."""
    from remarkable_ea import run_daily as daily_mod

    daily_mod.run(_load(ctx))


if __name__ == "__main__":
    cli()
