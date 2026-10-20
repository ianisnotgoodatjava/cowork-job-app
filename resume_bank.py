"""Loads resume_bank.json, the one copy of the resume everything else reads.

The file itself is personal -- home address, EEO answers, salary
expectations -- so it is not in the repository. resume_bank.example.json is,
with the same shape and placeholder values; copy it and fill it in.

apply_autofill, cover_letter and prep_package each opened the file
themselves, so a missing one surfaced as a bare FileNotFoundError three
different ways and said nothing about how to fix it.
"""

import json
import os

BANK_FILE = "resume_bank.json"
EXAMPLE_FILE = "resume_bank.example.json"


def load(path: str = BANK_FILE) -> dict:
    """The whole bank, or a message saying how to create it."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(
            f"No {path}.\n"
            "\n"
            "It holds your resume and is deliberately not in the repository --\n"
            "it carries your address, EEO answers and salary expectations.\n"
            "\n"
            f"  cp {EXAMPLE_FILE} {path}\n"
            "\n"
            "then fill it in. The example has the same shape with placeholders."
        )
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}")


def profile(path: str = BANK_FILE) -> dict:
    return load(path)["profile"]


def background(key: str, path: str = BANK_FILE) -> str:
    """A prompt-ready bio from the bank.

    These name employers and a bank he asks not to be named in drafts, so
    they live in the ignored file rather than in the prompts that use them.
    Missing one is fatal: a drafting prompt with no bio in it produces a
    confident email about nobody.
    """
    text = load(path).get("background", {}).get(key)
    if not text:
        raise SystemExit(
            f"No `background.{key}` in {path}. The prompt needs a short bio; "
            f"see {EXAMPLE_FILE} for the shape.")
    return text


def is_configured(path: str = BANK_FILE) -> bool:
    """True when a real bank exists. For callers that degrade rather than exit."""
    return os.path.exists(path)
