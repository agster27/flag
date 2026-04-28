"""
config.py — Central configuration loader for the flag project.

Defines install paths and provides load_config() for reading config.json.

**Python ≥ 3.9 is required** (uses ``zoneinfo`` from the standard library and
PEP 585 generic aliases such as ``list[str]`` in type annotations).

The ``speakers`` key in config.json may be either:

* **Legacy format** — a JSON array of IP address strings::

      "speakers": ["10.0.40.32", "10.0.40.41"]

* **New format** — a JSON array of objects with at minimum an ``"ip"`` field::

      "speakers": [
          {"ip": "10.0.40.32", "name": "Flag", "volume": 50},
          {"ip": "10.0.40.41", "name": "Backyard Left", "volume": 80},
          {"ip": "10.0.40.42", "name": "Backyard Right"}
      ]

In the new format each speaker may carry its own ``"volume"`` override; missing
overrides fall back to the top-level ``"volume"`` key (default 30).  Both formats
are accepted by :func:`load_config` and all downstream consumers.

Use :func:`speaker_ips` to extract a plain list of IP strings from either format.
"""
import json
import logging
import os

try:
    import zoneinfo
except ImportError:
    zoneinfo = None  # type: ignore[assignment]  # Python < 3.9

INSTALL_DIR = os.environ.get("FLAG_INSTALL_DIR", "/opt/flag")
CONFIG_PATH = os.environ.get("FLAG_CONFIG", os.path.join(INSTALL_DIR, "config.json"))
AUDIO_DIR = os.path.join(INSTALL_DIR, "audio")
LOG_FILE = os.path.join(INSTALL_DIR, "sonos_play.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
_log = logging.getLogger(__name__)

# Convenience accessor — available after load_config() has been called
def get_port(cfg: dict) -> int:
    """Return the configured HTTP port, falling back to 8000 on invalid values."""
    raw = cfg.get("port", 8000)
    try:
        return int(raw)
    except (ValueError, TypeError):
        _log.warning("Config 'port' value %r is not a valid integer; using default 8000.", raw)
        return 8000


def speaker_ips(config: dict) -> list:
    """
    Return a list of IP address strings from the ``speakers`` config key.

    Handles both the legacy string-array format and the new object format
    transparently, so callers that only need IPs don't have to branch.

    Args:
        config (dict): Configuration dictionary returned by :func:`load_config`.

    Returns:
        list[str]: Ordered list of speaker IP addresses.
    """
    return [s["ip"] if isinstance(s, dict) else s for s in config.get("speakers", [])]


def validate_config(cfg: dict) -> None:
    """
    Perform semantic validation of the configuration dictionary.

    Checks for required keys, correct value types, and sensible value ranges.
    Logs warnings for non-critical issues and errors for critical ones
    (e.g. missing ``speakers``).  Does **not** raise — callers decide whether
    to abort based on the log output.

    Key ``speakers`` must be a non-empty list of IP address strings.  Each IP
    is used by ``sonos_play.py`` to connect to a Sonos device at runtime.  When
    more than one IP is listed all speakers play the audio simultaneously via a
    temporary Sonos group (see ``sonos_play.py`` for the full multi-speaker
    playback flow).

    Args:
        cfg (dict): Configuration dictionary returned by :func:`load_config`.
    """
    # --- Required critical keys ---
    speakers = cfg.get("speakers")
    if speakers is None:
        _log.error("Config is missing required key 'speakers'.")
    elif not isinstance(speakers, list) or not speakers:
        _log.error("Config 'speakers' must be a non-empty list; got %r.", speakers)
    else:
        for i, spk in enumerate(speakers):
            if isinstance(spk, dict):
                ip = spk.get("ip", "")
                if not isinstance(ip, str) or not ip.strip():
                    _log.error(
                        "Config 'speakers[%d].ip' must be a non-empty string; got %r.", i, ip
                    )
                vol = spk.get("volume")
                if vol is not None and (not isinstance(vol, (int, float)) or not (0 <= vol <= 100)):
                    _log.warning(
                        "Config 'speakers[%d].volume' %r is invalid or outside 0–100.", i, vol
                    )
            elif isinstance(spk, str):
                if not spk.strip():
                    _log.error(
                        "Config 'speakers[%d]' must be a non-empty string; got %r.", i, spk
                    )
            else:
                _log.error(
                    "Config 'speakers[%d]' must be a string or object; got %r.", i, spk
                )

    if "volume" not in cfg:
        _log.error("Config is missing required key 'volume'.")
    elif not isinstance(cfg["volume"], (int, float)):
        _log.error("Config 'volume' must be a number; got %r.", cfg["volume"])
    elif not (0 <= cfg["volume"] <= 100):
        _log.warning("Config 'volume' %r is outside the valid range 0–100.", cfg["volume"])

    # --- Optional but type-checked keys ---
    port_raw = cfg.get("port")
    if port_raw is not None:
        try:
            port = int(port_raw)
            if not (1 <= port <= 65535):
                _log.warning("Config 'port' %r is outside the valid range 1–65535.", port_raw)
        except (ValueError, TypeError):
            _log.warning("Config 'port' value %r is not a valid integer.", port_raw)

    lat = cfg.get("latitude")
    if lat is not None:
        try:
            lat_f = float(lat)
            if not (-90 <= lat_f <= 90):
                _log.warning("Config 'latitude' %r is outside the valid range -90 to 90.", lat)
        except (ValueError, TypeError):
            _log.warning("Config 'latitude' value %r is not a valid number.", lat)

    lon = cfg.get("longitude")
    if lon is not None:
        try:
            lon_f = float(lon)
            if not (-180 <= lon_f <= 180):
                _log.warning("Config 'longitude' %r is outside the valid range -180 to 180.", lon)
        except (ValueError, TypeError):
            _log.warning("Config 'longitude' value %r is not a valid number.", lon)

    tz = cfg.get("timezone")
    if tz is not None:
        if zoneinfo is None:
            _log.debug(
                "Skipping timezone validation: zoneinfo module unavailable (Python < 3.9)."
            )
        else:
            try:
                zoneinfo.ZoneInfo(tz)
            except (LookupError, ValueError, TypeError):
                _log.warning(
                    "Config 'timezone' %r does not appear to be a valid IANA timezone.", tz
                )


def load_config(path=None):
    """
    Load and return the JSON configuration from config.json.

    Also calls :func:`validate_config` to log warnings/errors for any
    semantic issues found in the loaded configuration.

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
            cfg = json.load(f)
    except FileNotFoundError:
        raise RuntimeError(
            f"Config file not found: {path}\n"
            f"Please ensure config.json exists in {INSTALL_DIR}."
        )
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Config file contains invalid JSON: {path}\nDetails: {e}"
        )
    validate_config(cfg)
    return cfg
