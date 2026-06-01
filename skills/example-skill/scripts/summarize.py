#!/usr/bin/env -S uv run -q
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Print a small deterministic summary for the example skill."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="example-skill")
    parser.add_argument("--include-reference", action="store_true")
    args = parser.parse_args()

    print(f"skill: {args.name}")
    print("purpose: demonstrate SKILL.md, references, and scripts")
    if args.include_reference:
        print("reference: references/checklist.md")


if __name__ == "__main__":
    main()
