#!/usr/bin/env python3
"""Check pass rate against threshold. Reads from env vars only (no shell interpolation)."""

import os
import sys

pass_rate = float(os.environ.get("PASS_RATE", "0"))
threshold = float(os.environ.get("PASS_THRESHOLD", "80"))

if pass_rate < threshold:
    print(f"::error::Pass rate {pass_rate:.1f}% is below threshold {threshold:.1f}%")
    sys.exit(1)

print(f"Pass rate {pass_rate:.1f}% meets threshold {threshold:.1f}%")
