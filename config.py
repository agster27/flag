"""
config.py — Central configuration loader for the flag project.

Defines install paths and provides load_config() for reading config.json.
"""
import json
import os

INSTALL_DIR = "/opt/flag"
CONFIG_PATH = os.path.join(INSTALL_DIR, "config.json")
AUDIO_DIR = os.path.join(INSTALL_DIR, "audio")
LOG_FILE = os.path.join(INSTALL_DIR, "sonos_play.log")


def load_config(path=None):
    """
    Load and return the JSON configuration from config.json.

    Args:
        path (str, optional): Path to the config file. Defaults to CONFIG_PATH.

    Returns:
        dict: Parsed configuration dictionary.

    Raises:
        RuntimeError: If the config file is missing or contains invalid JSON.
    """
    if path is None:
        path = CONFIG_PATH
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise RuntimeError(
            f"Config file not found: {path}\n"
            f"Please ensure config.json exists in {INSTALL_DIR}."
        )
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Config file contains invalid JSON: {path}\nDetails: {e}"
        )
