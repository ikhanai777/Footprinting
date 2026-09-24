import sys

from .cli import main

if __name__ == "__main__":  # guard required for multiprocessing on Windows/macOS
    sys.exit(main())
