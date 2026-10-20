import multiprocessing
import sys

from snappy.app import main

if __name__ == "__main__":
    # The audio process (multiprocessing) relaunches the executable: freeze_support recognizes it.
    multiprocessing.freeze_support()
    sys.exit(main())
