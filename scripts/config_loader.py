"""
Tiny shim so the numbered scripts can import the shared config.

`00_config.py` is not a valid Python module name (starts with a digit), so we
load it by path and re-expose it as `cfg`.

Usage in any pipeline script:
    from config_loader import cfg
    print(cfg.WORK_CRS)
"""

import importlib.util
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "00_config.py")
_spec = importlib.util.spec_from_file_location("city_pipeline_config", _PATH)
cfg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfg)
