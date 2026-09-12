from __future__ import annotations

import argparse
from pathlib import Path

from uga.release.build_evidence import BuildQualificationReport, build_qualification_report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write or verify source-bound UGA build qualification evidence"
    )
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--launcher-smoke-passed", action="store_true")
    parser.add_argument("--verify-existing", type=Path)
    args = parser.parse_args()
    if args.verify_existing is not None:
        report = BuildQualificationReport.load(args.verify_existing)
        report.verify(args.bundle)
        print(args.verify_existing.resolve())
        return
    if args.source_revision is None or args.output is None:
        parser.error("--source-revision and --output are required when writing evidence")
    report = build_qualification_report(
        args.bundle,
        source_revision=args.source_revision,
        launcher_smoke_passed=args.launcher_smoke_passed,
    )
    print(report.write(args.output))


if __name__ == "__main__":
    main()
