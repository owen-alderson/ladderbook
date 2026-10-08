"""ladderbook: market making research for Kalshi's hourly crypto strike ladders."""

import argparse
import asyncio
import logging
from pathlib import Path

from ladderbook import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ladderbook", description=__doc__)
    parser.add_argument("--version", action="version", version=f"ladderbook {__version__}")
    commands = parser.add_subparsers(dest="command")

    rec = commands.add_parser("record", help="record Kalshi order books + Deribit options to Parquet")
    rec.add_argument("--out", type=Path, default=Path("data"))
    rec.add_argument("--series", default="KXBTCD,KXBTC,KXETHD,KXETH", help="comma-separated Kalshi series")
    rec.add_argument("--demo", action="store_true", help="use Kalshi's demo environment")
    rec.add_argument("--rotate", type=float, default=300.0, help="seconds per Parquet file")

    ver = commands.add_parser("verify", help="rebuild every recorded book and check it against Kalshi's snapshots")
    ver.add_argument("--data", type=Path, default=Path("data"))

    args = parser.parse_args(argv)
    if args.command == "record":
        from ladderbook.record.recorder import record

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        try:
            asyncio.run(record(args.out, args.series.split(","), demo=args.demo, rotate_seconds=args.rotate))
        except KeyboardInterrupt:
            pass
    elif args.command == "verify":
        from ladderbook.record.verify import verify

        report = verify(args.data)
        print(report.summary())
        raise SystemExit(0 if report.ok else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
