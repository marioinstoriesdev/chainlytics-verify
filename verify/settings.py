"""Configuration loading. All tunable values come from config.yaml."""
import os

import yaml

CONFIG_ENV = "CHAINLYTICS_VERIFY_CONFIG"
LOCAL_ENV = "CHAINLYTICS_VERIFY_LOCAL_CONFIG"
LOCAL_NAME = "config.local.yaml"


def default_path():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get(CONFIG_ENV) or os.path.join(here, "config.yaml")


def local_path(path):
    """Untracked file for secrets (bot token and the like), merged over config.yaml at load."""
    return os.environ.get(LOCAL_ENV) or os.path.join(os.path.dirname(os.path.abspath(path)), LOCAL_NAME)


def merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None):
    p = path or default_path()
    with open(p, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    lp = local_path(p)
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as f:
            merge(cfg, yaml.safe_load(f) or {})
    return cfg


def bootstrap(cfg):
    """Create the data folders."""
    os.makedirs(os.path.dirname(os.path.abspath(cfg["paths"]["db"])), exist_ok=True)
    os.makedirs(cfg["paths"]["reports_dir"], exist_ok=True)
    return cfg
