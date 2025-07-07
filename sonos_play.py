#!/usr/bin/env python3
"""
Sonos Play Script - Unified Group-Based Approach

This script plays audio on Sonos speakers using a unified group-based approach.
Whether you specify a single speaker or multiple speakers in the 'group_speakers' 
configuration, the script treats them as a group for consistent behavior.

Key Features:
- Automatic speaker discovery on the network
- Unified handling of single and multiple speakers
- Group coordination and management
- State preservation and restoration
- Backwards compatibility with old config formats

Usage: python sonos_play.py <mp3_url>
Config: Uses 'group_speakers' array in config.json

The script will:
1. Discover all Sonos speakers on the network
2. Find the speakers specified in group_speakers
3. Group them together (if multiple)
4. Take a snapshot of the current state
5. Play the specified audio
6. Wait for playback to complete
7. Restore the previous state
"""

import soco
import sys
import time
import json
import urllib.request
import os
from datetime import datetime
from mutagen.mp3 import MP3
from soco.snapshot import Snapshot

# Check for required arguments first
if len(sys.argv) < 2:
    print("Usage: python sonos_play.py <mp3_url>")
    sys.exit(1)

# Load config with fallback for testing
try:
    config_path = "/opt/flag/config.json"
    if not os.path.exists(config_path):
        config_path = "config.json"
    
    with open(config_path) as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"Error: Config file not found at {config_path}")
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f"Error: Invalid JSON in config file: {e}")
    sys.exit(1)

# Use group_speakers for unified approach - treat single or multiple speakers as a group
GROUP_SPEAKERS = config.get("group_speakers", [])
if not GROUP_SPEAKERS:
    # Fallback to old sonos_ip for backwards compatibility during transition
    if "sonos_ip" in config:
        # Convert old format: assume the IP represents a speaker that needs discovery
        GROUP_SPEAKERS = [config["sonos_ip"]]
    else:
        print("Error: No group_speakers specified in config.json")
        sys.exit(1)

VOLUME = config["volume"]
SKIP_RESTORE_IF_IDLE = config.get("skip_restore_if_idle", True)
DEFAULT_WAIT = config.get("default_wait_seconds", 60)
LOG_FILE = "/opt/flag/sonos_play.log" if os.path.exists("/opt/flag") else "sonos_play.log"
AUDIO_URL = sys.argv[1]

def log(message):
    """Log message to file with timestamp"""
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} - {message}\n")

def get_mp3_duration(url):
    """Get MP3 duration in seconds"""
    try:
        temp_file = "/tmp/temp_scheduled_song.mp3"
        urllib.request.urlretrieve(url, temp_file)
        audio = MP3(temp_file)
        return int(audio.info.length)
    except Exception as e:
        log(f"WARNING: Could not get duration. Defaulting to {DEFAULT_WAIT} sec. Error: {e}")
        return DEFAULT_WAIT

def discover_speakers():
    """Discover all Sonos speakers on the network"""
    try:
        devices = soco.discover()
        if not devices:
            raise Exception("No Sonos speakers found on network")
        
        speakers = {}
        for device in devices:
            speakers[device.player_name] = device
            # Also index by IP for backwards compatibility
            speakers[device.ip_address] = device
            
        log(f"INFO: Discovered {len(devices)} Sonos speakers")
        return speakers
    except Exception as e:
        raise Exception(f"Failed to discover Sonos speakers: {e}")

def find_target_speakers(speakers):
    """Find target speakers from the discovered speakers"""
    target_speakers = []
    missing_speakers = []
    
    for speaker_identifier in GROUP_SPEAKERS:
        if speaker_identifier in speakers:
            target_speakers.append(speakers[speaker_identifier])
        else:
            missing_speakers.append(speaker_identifier)
    
    if missing_speakers:
        available = [f"{s.player_name} ({s.ip_address})" for s in speakers.values() if hasattr(s, 'player_name')]
        raise Exception(f"Target speakers not found: {missing_speakers}. Available: {available}")
    
    log(f"INFO: Found target speakers: {[s.player_name for s in target_speakers]}")
    return target_speakers

def get_group_coordinator(speakers):
    """Get or create a group coordinator from the target speakers"""
    if len(speakers) == 1:
        # Single speaker - treat as its own group
        return speakers[0].group.coordinator
    
    # Multiple speakers - group them together using the first as coordinator
    main_speaker = speakers[0]
    coordinator = main_speaker.group.coordinator
    
    # Group all speakers with the main one
    for speaker in speakers[1:]:
        if speaker.group.coordinator != coordinator:
            try:
                speaker.join(main_speaker)
                log(f"INFO: Grouped {speaker.player_name} with {main_speaker.player_name}")
                time.sleep(1)  # Allow time for group to form
            except Exception as e:
                log(f"WARNING: Failed to group {speaker.player_name}: {e}")
    
    # Return the coordinator (which should be the first speaker's coordinator)
    return main_speaker.group.coordinator

try:
    log(f"INFO: Starting playback for {AUDIO_URL}")
    
    # Discover all speakers on the network
    all_speakers = discover_speakers()
    
    # Find our target speakers
    target_speakers = find_target_speakers(all_speakers)
    
    # Get or create group coordinator
    coordinator = get_group_coordinator(target_speakers)
    
    # Check current state
    state = coordinator.get_current_transport_info()["current_transport_state"]
    was_playing = state == "PLAYING"
    
    # Take snapshot before making changes
    snapshot = Snapshot(coordinator)
    snapshot.snapshot()
    log(f"INFO: Took snapshot of group led by {coordinator.player_name} (was_playing={was_playing})")
    
    # Stop current playback and set volume
    coordinator.stop()
    coordinator.volume = VOLUME
    
    # Play the requested audio
    coordinator.play_uri(AUDIO_URL)
    log(f"SUCCESS: Played {AUDIO_URL} on group led by {coordinator.player_name}")
    
    # Wait for playback to finish
    duration = get_mp3_duration(AUDIO_URL)
    log(f"INFO: Waiting {duration} seconds for playback to finish")
    time.sleep(duration)
    
    # Restore previous state if needed
    if was_playing or not SKIP_RESTORE_IF_IDLE:
        snapshot.restore()
        log(f"INFO: Restored previous playback on group led by {coordinator.player_name}")
    else:
        log("INFO: No prior playback. Skipping restore.")

except Exception as e:
    log(f"ERROR: Failed during scheduled play - {e}")
