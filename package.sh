#!/bin/sh
# Rebuild code.zip from the repository root: sh package.sh
# Contents: README.md, code/ (source, tests, cache, code/evaluation/ incl. reports) and evaluation/ (the two
# reports at the top-level path required by AGENTS.md). dataset/, caches, venvs and archives are excluded.
set -e
cd "$(dirname "$0")"
find . -name __pycache__ -not -path "./.git/*" -prune -exec rm -rf {} +
rm -rf .pytest_cache code.zip
zip -r -q code.zip README.md code evaluation \
  -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store" "*/.pytest_cache/*" "*.zip"
unzip -l code.zip
