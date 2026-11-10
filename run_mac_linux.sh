#!/usr/bin/env bash
# LED Wall Creator - run from the source folder on macOS / Linux.
# First run: creates a private Python environment and installs the app's libraries (a few minutes).
set -e
cd "$(dirname "$0")/python"
PY=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
    echo "Setting up LED Wall Creator for the first time - this takes a few minutes..."
    if ! "$PY" -m venv .venv; then
        echo "Python 3.10 or newer (with venv) is needed."
        echo "  macOS:  https://www.python.org/downloads/   or   brew install python"
        echo "  Ubuntu: sudo apt install python3 python3-venv"
        exit 1
    fi
fi
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
exec .venv/bin/python led_wall_motion_generator.py "$@"
