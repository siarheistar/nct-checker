#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print 'Set up the Python environment described in README.md first.'
  read 'reply?Press Return to close.'
  exit 1
fi
exec .venv/bin/python -u local_browser_search.py
