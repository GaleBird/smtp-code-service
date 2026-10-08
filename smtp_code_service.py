#!/usr/bin/env python3
"""
Backward-compatibility entrypoint for smtp-code-service.
Redirects execution to app.main:main.
"""
import sys
from app.main import main

if __name__ == "__main__":
    sys.exit(main())
