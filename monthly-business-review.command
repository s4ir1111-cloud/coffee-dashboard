#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 build_monthly_report.py "$@"
