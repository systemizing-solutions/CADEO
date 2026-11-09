"""Allow `python -m cadeo` as an alternative CLI invocation."""
from cadeo.exposers.cli import main

raise SystemExit(main())
