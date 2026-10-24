"""run.bat: starts Snappy from the sources without a console window (see system.launch_command).

    .venv\\Scripts\\python.exe -m snappy.platforms.windows.launch [Snappy options]
"""

import subprocess
import sys

from .system import launch_command

if __name__ == "__main__":
    subprocess.Popen(launch_command(sys.argv[1:]), close_fds=True)
