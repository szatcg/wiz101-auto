"""Command line entry point: `wiz101-auto run|inspect`."""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from .config import load_config


def _setup_logging(log_file: str | None, verbose: bool):
    logger.remove()
    fmt = "<green>{time:HH:mm:ss}</green> {message}"
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO", format=fmt)
    if log_file:
        logger.add(log_file, level="DEBUG", rotation="10 MB", retention=5)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="wiz101-auto", description="Autonomous Wizard101 bot")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the bot")
    run_p.add_argument("-c", "--config", default=None, help="path to config YAML")
    run_p.add_argument("-m", "--mode", choices=["quest", "fight", "farm"], help="override config mode")
    run_p.add_argument("-v", "--verbose", action="store_true")

    insp = sub.add_parser("inspect", help="print what the bot sees (state, battle, UI)")
    insp.add_argument("--windows", action="store_true", help="also dump the visible UI window tree")

    args = parser.parse_args(argv)

    if sys.platform != "win32":
        raise SystemExit("wiz101-auto talks to the Windows game client and must run on Windows.")

    if args.command == "inspect":
        _setup_logging(None, True)
        from .inspect_state import inspect

        asyncio.run(inspect(show_windows=args.windows))
        return

    cfg = load_config(args.config)
    if args.mode:
        cfg.mode = args.mode
    _setup_logging(cfg.log_file, args.verbose)

    from .bot import run

    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        logger.info("interrupted")


if __name__ == "__main__":
    main()
