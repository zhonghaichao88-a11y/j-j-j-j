"""Historical runner superseded by V73_RUN_REPLAY.py."""
import runpy
from pathlib import Path
if __name__ == "__main__": runpy.run_path(str(Path(__file__).with_name("V73_RUN_REPLAY.py")),run_name="__main__")
