import json
import os

INSTALL_DIR = "/opt/flag"
CONFIG_PATH = os.path.join(INSTALL_DIR, "config.json")
AUDIO_DIR = os.path.join(INSTALL_DIR, "audio")
LOG_FILE = os.path.join(INSTALL_DIR, "sonos_play.log")


def load_config(path=None):
    if path is None:
        path = CONFIG_PATH
    with open(path) as f:
        return json.load(f)
