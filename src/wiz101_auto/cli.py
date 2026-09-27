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


def _lower_priority():
    """Run below normal priority so the bot can never starve the game or the PC."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
    except Exception:
        pass


def main(argv: list[str] | None = None):
    # Game text (window titles, names) can hold characters the Windows console
    # encoding lacks; print a placeholder instead of crashing `status`/`inspect`.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="wiz101-auto", description="Autonomous Wizard101 bot")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the bot")
    run_p.add_argument("-c", "--config", default=None, help="path to config YAML")
    run_p.add_argument("-m", "--mode", choices=["quest", "fight", "farm"], help="override config mode")
    run_p.add_argument("-v", "--verbose", action="store_true")

    insp = sub.add_parser("inspect", help="print what the bot sees (state, battle, UI)")
    insp.add_argument("--windows", action="store_true", help="also dump the visible UI window tree")

    deck_p = sub.add_parser("deck", help="show the planned deck from known spells, optionally apply it")
    deck_p.add_argument("-c", "--config", default=None)
    deck_p.add_argument("--apply", action="store_true", help="actually rebuild the in-game deck")

    sub.add_parser("explore", help="save nearby NPCs/doors/mobs and their positions for this zone")
    shot_p = sub.add_parser("screenshot", help="save the game window as a PNG (works while the bot runs)")
    shot_p.add_argument("-o", "--output", default="state/screenshot.png")

    start_p = sub.add_parser("start", help="start the bot in the background")
    start_p.add_argument("-c", "--config", default="config.yaml")
    start_p.add_argument("--supervise", action="store_true", help="restart automatically after crashes")
    restart_p = sub.add_parser("restart", help="stop, then start again in the background")
    restart_p.add_argument("-c", "--config", default="config.yaml")
    restart_p.add_argument("--supervise", action="store_true")
    sub.add_parser("stop", help="stop the background bot cleanly")
    sub.add_parser("status", help="is it running, what is it doing, recent log lines")
    logs_p = sub.add_parser("logs", help="show the log")
    logs_p.add_argument("-n", type=int, default=80)
    logs_p.add_argument("-f", "--follow", action="store_true")
    sup_p = sub.add_parser("supervise", help="run in the foreground, restarting after crashes")
    sup_p.add_argument("-c", "--config", default="config.yaml")

    args = parser.parse_args(argv)

    from . import service

    if args.command == "start":
        sys.exit(service.start(args.config, args.supervise))
    if args.command == "stop":
        sys.exit(service.stop())
    if args.command == "restart":
        service.stop()
        sys.exit(service.start(args.config, args.supervise))
    if args.command == "status":
        sys.exit(service.status())
    if args.command == "logs":
        sys.exit(service.logs(args.n, args.follow))
    if args.command == "supervise":
        sys.exit(service.supervise(args.config))

    if sys.platform != "win32":
        raise SystemExit("wiz101-auto talks to the Windows game client and must run on Windows.")
    _lower_priority()

    if args.command == "inspect":
        _setup_logging(None, True)
        from .inspect_state import inspect

        asyncio.run(inspect(show_windows=args.windows))
        return

    if args.command == "screenshot":
        from .screenshot import save_screenshot

        print(save_screenshot(args.output))
        return

    if args.command == "explore":
        _setup_logging(None, True)
        from .explore import explore

        asyncio.run(explore())
        return

    cfg = load_config(args.config)

    if args.command == "deck":
        _setup_logging(None, True)
        asyncio.run(_deck(cfg, args.apply))
        return

    if args.mode:
        cfg.mode = args.mode
    _setup_logging(cfg.log_file, args.verbose)

    from .bot import run

    try:
        reason = asyncio.run(run(cfg)) or ""
    except KeyboardInterrupt:
        logger.info("interrupted")
        return
    if "crashed" in reason:
        sys.exit(3)  # lets `supervise` restart it


async def _deck(cfg, apply: bool):
    from .bot import close_handler, connect, new_handler
    from .deck import current_school, rebuild_deck

    handler = new_handler()
    try:
        client = await connect(handler)
        school = cfg.progression.school or await current_school(client)
        async with client.mouse_handler:
            known, plan = await rebuild_deck(client, school, cfg.progression.deck, dry_run=not apply)
        print(f"\nschool: {school}\nknown spells ({len(known)}):")
        for s in known:
            effects = ", ".join(f"{e.kind.name}:{e.value:g}" for e in s.card.effects)
            print(f"  {s.name} [{s.card.school}] {s.card.pip_cost}p max {s.max_copies} -> {effects}")
        print(f"\ndeck plan: {plan.describe()}")
        if not apply:
            print("(dry run; add --apply to rebuild the in-game deck)")
    finally:
        await close_handler(handler)


if __name__ == "__main__":
    main()
