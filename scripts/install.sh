#!/bin/sh
set -eu
taskman_source=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
taskman_install=${TASKMAN_INSTALL_DIR:-"${XDG_DATA_HOME:-$HOME/.local/share}/taskman/app"}
taskman_python=${PYTHON:-python3}
"$taskman_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
"$taskman_python" -m venv "$taskman_install/venv"
"$taskman_install/venv/bin/python" -m pip install --upgrade "$taskman_source"
printf '\nInstalled Taskman. Launch with: "%s/venv/bin/taskman"\n' "$taskman_install"
if [ "${TASKMAN_NO_LAUNCH:-0}" != 1 ]; then
  exec "$taskman_install/venv/bin/taskman" "$@"
fi
