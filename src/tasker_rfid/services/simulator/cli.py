"""The `sim` command — a fake RFID reader. SPEC.md section 5.1.

    sim box     --tid A7F3 --portal ENTRANCE
    sim pallet  --boxes 50 --portal EXIT --miss-rate 0.08
    sim stray   --tid B21C --duration 600
    sim reverse --tid C99A --portal ENTRANCE
    sim burst   --count 900

Add --dry-run to any of them to print the messages instead of publishing,
which needs no broker and is the quickest way to see the format.
"""

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from ...config import antennas_for_portal, gate_id_for_portal
from .model import (
    BOX_BEAM_BLOCKED_S,
    PALLET_BEAM_BLOCKED_S,
    GateEvent,
    Read,
    Tag,
    attempt_hz_per_tag,
    gate_crossing,
    merge,
    pass_reads,
    stray_reads,
)
from .publisher import gate_messages, print_messages, publish_messages, read_messages

PORTALS = ["ENTRANCE", "EXIT"]

PALLET_ATTENUATION_DB = 3.0
"""A pallet tag rides on the outside of the stack, so it reads nearly clean."""

BOX_ATTENUATION_MAX_DB = 12.0
"""A box buried in the middle of a stack is shadowed by the boxes around it."""

PALLET_STAGGER_S = 0.25
"""Boxes on one pallet do not all cross the exact same instant."""


def load_antennas(portal: str) -> list[int]:
    """Which antennas belong to this portal, from config/tasker.yaml."""
    return antennas_for_portal(portal)


def crossing_for(
    portal: str,
    start: datetime,
    rng: random.Random,
    *,
    blocked_s: float,
    reverse: bool = False,
) -> list[GateEvent]:
    """Beam events for a portal that has an IR gate; nothing for one that does not.

    Only the exit is gated. SPEC.md section 4: the entrance resolves
    direction from the state machine alone, so a pass through the entrance
    produces tag reads and no beam events at all.
    """
    gate_id = gate_id_for_portal(portal)
    if not gate_id:
        return []
    return gate_crossing(gate_id, start, rng, blocked_s=blocked_s, reverse=reverse)


def make_tid(rng: random.Random) -> str:
    """A plausible factory-locked chip serial: 24 hex characters.

    E280 1170 is a real Impinj Monza manufacturer prefix, so simulated TIDs
    look like the ones the hardware will produce.
    """
    return "E28011702000" + "".join(rng.choice("0123456789ABCDEF") for _ in range(12))


def make_epc(tid: str) -> str:
    """An EPC derived from the TID.

    SPEC.md 2.1: the EPC is informational only, nothing is ever written to a
    tag and nothing keys off this value. It exists so the message shape
    matches what a real reader sends.
    """
    return "3034" + tid[-20:]


def make_tag(tid: str | None, rng: random.Random, attenuation_db: float = 0.0) -> Tag:
    resolved = tid or make_tid(rng)
    return Tag(tid=resolved, epc=make_epc(resolved), attenuation_db=attenuation_db)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------


def cmd_box(args: argparse.Namespace, rng: random.Random) -> tuple[list[Read], list[GateEvent]]:
    """One container carried through one portal, the ordinary case."""
    antennas = load_antennas(args.portal)
    tag = make_tag(args.tid, rng)
    start = now_utc()
    reads = pass_reads(tag, args.reader_id, antennas, start, rng)
    gates = crossing_for(args.portal, start, rng, blocked_s=BOX_BEAM_BLOCKED_S)
    print(
        f"box {tag.tid} through {args.portal} on antennas {antennas}"
        + (f", breaking {len(gates)//2} beams (outbound)" if gates else ", no IR gate"),
        file=sys.stderr,
    )
    return reads, gates


def cmd_reverse(args: argparse.Namespace, rng: random.Random) -> tuple[list[Read], list[GateEvent]]:
    """A container carried back the way it came.

    Identical to `box` except everything happens in the opposite order: the
    antennas peak the other way round, and at a gated portal the beams break
    the other way round too. SPEC.md section 7 expects this to end up as an
    ILLEGAL_TRANSITION anomaly.
    """
    antennas = load_antennas(args.portal)
    tag = make_tag(args.tid, rng)
    start = now_utc()
    reads = pass_reads(tag, args.reader_id, antennas, start, rng, reverse=True)
    gates = crossing_for(
        args.portal, start, rng, blocked_s=BOX_BEAM_BLOCKED_S, reverse=True
    )
    print(
        f"reverse pass of {tag.tid} through {args.portal} on antennas {antennas}"
        + (
            f", breaking {len(gates)//2} beams (inbound)"
            if gates
            else ", no IR gate at this portal"
        ),
        file=sys.stderr,
    )
    return reads, gates


def cmd_stray(args: argparse.Namespace, rng: random.Random) -> tuple[list[Read], list[GateEvent]]:
    """A box parked within range of an antenna, read over and over."""
    antennas = load_antennas(args.portal)
    tag = make_tag(args.tid, rng)
    print(
        f"stray {tag.tid} parked at {args.portal} for {args.duration}s "
        f"on antennas {antennas}",
        file=sys.stderr,
    )
    # A parked box never crosses the beams; that is exactly what makes it a
    # stray rather than a movement.
    return stray_reads(tag, args.reader_id, antennas, now_utc(), args.duration, rng), []


def cmd_pallet(args: argparse.Namespace, rng: random.Random) -> tuple[list[Read], list[GateEvent]]:
    """A loaded pallet through a portal: the pallet tag plus its boxes.

    Each box has a --miss-rate chance of producing no reads at all — shadowed
    by the load, facing the wrong way, or tag damaged. That is what drives the
    SHORT_PALLET anomaly in SPEC.md section 7.
    """
    antennas = load_antennas(args.portal)
    start = now_utc()

    pallet_tag = make_tag(args.tid, rng, attenuation_db=PALLET_ATTENUATION_DB)
    box_tags = [
        make_tag(None, rng, attenuation_db=rng.uniform(0, BOX_ATTENUATION_MAX_DB))
        for _ in range(args.boxes)
    ]

    present = [tag for tag in box_tags if rng.random() >= args.miss_rate]
    missed = len(box_tags) - len(present)

    # The reader splits its attention across everything in the field at once.
    hz = attempt_hz_per_tag(len(present) + 1)

    streams = [pass_reads(pallet_tag, args.reader_id, antennas, start, rng, attempt_hz=hz)]
    for tag in present:
        offset = start + timedelta(
            seconds=rng.uniform(-PALLET_STAGGER_S, PALLET_STAGGER_S)
        )
        streams.append(pass_reads(tag, args.reader_id, antennas, offset, rng, attempt_hz=hz))

    # One pallet is one crossing, however many tags ride on it.
    gates = crossing_for(args.portal, start, rng, blocked_s=PALLET_BEAM_BLOCKED_S)

    print(
        f"pallet {pallet_tag.tid} through {args.portal} on antennas {antennas}: "
        f"{args.boxes} boxes declared, {len(present)} readable, {missed} missed "
        f"(miss-rate {args.miss_rate})"
        + (", one gate crossing (outbound)" if gates else ", no IR gate"),
        file=sys.stderr,
    )
    return merge(*streams), gates


def cmd_burst(args: argparse.Namespace, rng: random.Random) -> tuple[list[Read], list[GateEvent]]:
    """Throughput test: a flood of reads, to prove nothing is dropped.

    SPEC.md section 10 acceptance criterion 4 is 900 reads/sec sustained
    without loss, which is why --count defaults to 900 over one second.
    """
    antennas = load_antennas(args.portal)
    start = now_utc()
    tags = [make_tag(None, rng) for _ in range(args.tags)]

    reads = []
    for n in range(args.count):
        tag = rng.choice(tags)
        elapsed = args.duration * n / args.count
        reads.append(
            Read(
                tid=tag.tid,
                epc=tag.epc,
                reader_id=args.reader_id,
                antenna_id=rng.choice(antennas),
                rssi=round(rng.uniform(-70, -45)),
                read_at=start + timedelta(seconds=elapsed),
            )
        )

    print(
        f"burst of {args.count} reads from {args.tags} tags over "
        f"{args.duration}s ({args.count / args.duration:.0f} reads/sec)",
        file=sys.stderr,
    )
    # A throughput test, not a movement: no gate crossing.
    return reads, []


COMMANDS = {
    "box": cmd_box,
    "pallet": cmd_pallet,
    "stray": cmd_stray,
    "reverse": cmd_reverse,
    "burst": cmd_burst,
}


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def add_common_arguments(parser: argparse.ArgumentParser, default_portal: str) -> None:
    parser.add_argument(
        "--portal",
        choices=PORTALS,
        default=default_portal,
        help=f"which portal the reads come from (default: {default_portal})",
    )
    parser.add_argument("--reader-id", default=None, help="default: READER-<portal>")
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="1.0 publishes in real time; 60 compresses a minute into a second; "
        "0 publishes as fast as possible (default: 1.0)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="fix the random seed so a run can be repeated exactly",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the messages instead of publishing them (no broker needed)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sim", description="Fake RFID reader. See SPEC.md section 5.1."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    box = subparsers.add_parser("box", help="one container through a portal")
    box.add_argument("--tid", default=None, help="default: a random plausible TID")
    add_common_arguments(box, "ENTRANCE")

    pallet = subparsers.add_parser("pallet", help="a loaded pallet through a portal")
    pallet.add_argument("--tid", default=None, help="TID of the pallet itself")
    pallet.add_argument("--boxes", type=int, default=50, help="boxes on the pallet")
    pallet.add_argument(
        "--miss-rate",
        type=float,
        default=0.08,
        help="fraction of boxes that produce no reads at all (default: 0.08)",
    )
    add_common_arguments(pallet, "EXIT")

    stray = subparsers.add_parser("stray", help="a box parked near an antenna")
    stray.add_argument("--tid", default=None)
    stray.add_argument(
        "--duration", type=float, default=600, help="seconds parked (default: 600)"
    )
    add_common_arguments(stray, "ENTRANCE")

    reverse = subparsers.add_parser("reverse", help="a container carried back out")
    reverse.add_argument("--tid", default=None)
    add_common_arguments(reverse, "ENTRANCE")

    burst = subparsers.add_parser("burst", help="throughput test")
    burst.add_argument("--count", type=int, default=900, help="reads to publish")
    burst.add_argument(
        "--duration", type=float, default=1.0, help="seconds to spread them over"
    )
    burst.add_argument("--tags", type=int, default=50, help="how many distinct tags")
    add_common_arguments(burst, "EXIT")

    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()

    if args.reader_id is None:
        args.reader_id = f"READER-{args.portal}"

    if getattr(args, "miss_rate", 0) < 0 or getattr(args, "miss_rate", 0) > 1:
        sys.exit("ERROR: --miss-rate must be between 0 and 1")
    if args.speed < 0:
        sys.exit("ERROR: --speed cannot be negative")

    rng = random.Random(args.seed)
    reads, gates = COMMANDS[args.command](args, rng)

    # Reads and beam events go onto one timeline, so the crossing lands in
    # the middle of the reads it belongs to.
    messages = read_messages(reads, os.getenv("MQTT_TOPIC", "tasker/reads"))
    messages += gate_messages(gates, os.getenv("MQTT_GATE_TOPIC", "tasker/gates"))
    messages.sort(key=lambda m: m.at)

    if args.dry_run:
        print_messages(messages)
        print(
            f"{len(reads)} reads + {len(gates)} beam events "
            "(dry run, nothing published)",
            file=sys.stderr,
        )
        return

    publish_messages(
        messages,
        host=os.getenv("MQTT_HOST", "localhost"),
        port=int(os.getenv("MQTT_PORT", "1883")),
        username=os.getenv("MQTT_USERNAME", ""),
        password=os.getenv("MQTT_PASSWORD", ""),
        speed=args.speed,
    )


if __name__ == "__main__":
    main()
