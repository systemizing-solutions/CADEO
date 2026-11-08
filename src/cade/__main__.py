"""Allow `python -m cade` as an alternative CLI invocation."""
from cade.exposers.cli import main

raise SystemExit(main())
