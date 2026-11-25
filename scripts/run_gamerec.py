"""PyInstaller entry point.

The explicit imports make PyInstaller bundle modules that the app only imports
lazily (otherwise they'd be missing from the exe).
"""
import sys

import gamerec.cli
import gamerec.env_check  # noqa: F401
import gamerec.sysinfo  # noqa: F401
import gamerec.hotkeys  # noqa: F401
import gamerec.inputs.logger  # noqa: F401
import gamerec.memory.scanner  # noqa: F401
import gamerec.memory.watcher  # noqa: F401
import gamerec.net.capture  # noqa: F401
import gamerec.ui.app  # noqa: F401
import gamerec.ui.env_dialog  # noqa: F401
import gamerec.ui.hotkey_dialog  # noqa: F401
import gamerec.video.recorder  # noqa: F401

sys.exit(gamerec.cli.main())
