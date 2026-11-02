#!/bin/sh
cd "$(dirname "$0")"
PYTHONDONTWRITEBYTECODE=1 python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else \"python3 is older than 3.10\")" || exit 1
python3 -m pip install -q -r requirements.txt
PYTHONDONTWRITEBYTECODE=1 python3 -m protocol_to_usdm serve "$@"
