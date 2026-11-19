"""Command-line entry point for the job-application tracker.

Typical flow:
  1. python cli.py log-application --job-title ... --company ... --requirements ... --location ... --link ...
  2. python cli.py find-contact-link --company "Acme Corp"      # opens manually in your browser
  3. python cli.py save-contact --row 2 --name "Jane Doe" --title "Engineering Manager" --linkedin-url "..."
  4. python cli.py draft-outreach-email --row 2 --to jane@acme.com --subject "..." --body-file outreach.txt
  5. python cli.py draft-application-email --row 2 --to jobs@acme.com --subject "..." --body-file cover_letter.txt --attach resume.pdf

Or just: python cli.py auto     — the nightly pipeline, which is what the
launchd job runs.

This file builds the parser from console/commands.py and dispatches. It used
to be 951 lines, 378 of them argparse, because every command repeated the
same three calls. The commands are a table now; the handlers are in
console/handlers.py; nothing here knows what any command does.
"""

import argparse

from dotenv import load_dotenv

load_dotenv()

from console.commands import COMMANDS, GROUPS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Job application tracker CLI",
        epilog=_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # The sixty-one names made a usage line nobody could read. The epilog
    # lists them by group instead, and the metavar keeps argparse from
    # printing the whole set a second time.
    sub = parser.add_subparsers(dest="command", required=True,
                                metavar="<command>")
    for command in COMMANDS:
        p = sub.add_parser(command.name, help=command.help)
        for arg in command.args:
            p.add_argument(*arg.flags, **arg.kwargs)
        p.set_defaults(_command=command)
    return parser


def _epilog() -> str:
    out = ["commands, by what they do:"]
    for group, what in GROUPS.items():
        out.append(f"\n  {group} — {what}")
        names = [c.name for c in COMMANDS if c.group == group]
        line = "   "
        for name in names:
            if len(line) + len(name) + 2 > 76:
                out.append(line)
                line = "   "
            line += f" {name}"
        out.append(line)
    out.append("\n  python cli.py <command> --help  for one command's options")
    return "\n".join(out)


def main() -> None:
    args = build_parser().parse_args()
    args._command.handler()(args)


if __name__ == "__main__":
    main()
