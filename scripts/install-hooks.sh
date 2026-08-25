#!/bin/sh
# Point this repository's git hooks at the tracked hooks/ directory.
# Run once after cloning:  sh scripts/install-hooks.sh
set -e
repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
chmod +x hooks/pre-commit
git config core.hooksPath hooks
echo "hooks installed: core.hooksPath -> hooks/"
python3 scripts/check_policy.py
