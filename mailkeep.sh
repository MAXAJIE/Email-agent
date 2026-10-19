#!/usr/bin/env sh
# Mailkeep launcher (macOS/Linux). Example: ./mailkeep.sh ui   or   ./mailkeep.sh ui --demo
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "No virtualenv. Create one: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
exec .venv/bin/python -m mailkeep "$@"
