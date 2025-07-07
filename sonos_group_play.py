#!/usr/bin/env python3
"""
Sonos Group Play Script

Plays a specified MP3 on two Sonos speakers with intelligent grouping logic.
Handles various scenarios including idle speakers, grouped speakers, and playing speakers.

The script implements the following logic:
1. If both speakers are idle and not grouped: Group them, play MP3, ungroup
2. If both are idle but in groups: Save group state, regroup together, play MP3, restore groups
3. If one/both are playing: Take full snapshots, regroup, play MP3, restore snapshots
4. If already grouped together and idle: Just play MP3
5. If already grouped together and playing: Take snapshot, play MP3, restore
6. If grouped with others: Save group state, regroup targets, play MP3, restore groups

Usage: python sonos_group_play.py <mp3_url>

Example: python sonos_group_play.py http://flag.aghy.home:8000/colors.mp3
"""

import sys
import time
import json
import urllib.request
from datetime import datetime

# Check for required arguments first
if len(sys.argv) < 2:
    print("Usage: python sonos_group_play.py <mp3_url>")
    sys.exit(1)

# Try to import Sonos dependencies
try:
    import soco
    from mutagen.mp3 import MP3
    from soco.snapshot import Snapshot
except ImportError as e:
    print(f"Error: Missing required dependencies: {e}")
    print("Please install requirements: pip install -r requirements.txt")
    sys.exit(1)

# Load config
try:
    config_path = "/opt/flag/config.json"
    # For testing, also try local config
    import os
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

GROUP_SPEAKERS = config.get("group_speakers", ["sonos-flag", "sonos-backyard"])
VOLUME = config["volume"]
SKIP_RESTORE_IF_IDLE = config.get("skip_restore_if_idle", True)
DEFAULT_WAIT = config.get("default_wait_seconds", 60)
LOG_FILE = "/opt/flag/sonos_play.log"
AUDIO_URL = sys.argv[1]

def log(message):
    """Log message to file with timestamp"""
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} - {message}\n")

def get_mp3_duration(url):
    """Get MP3 duration in seconds"""
    try:
        temp_file = "/tmp/temp_group_song.mp3"
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
            
        log(f"INFO: Discovered {len(speakers)} Sonos speakers: {list(speakers.keys())}")
        return speakers
    except Exception as e:
        raise Exception(f"Failed to discover Sonos speakers: {e}")

def find_target_speakers(speakers):
    """Find the two target speakers from discovered speakers"""
    target_speakers = {}
    missing_speakers = []
    
    for speaker_name in GROUP_SPEAKERS:
        if speaker_name in speakers:
            target_speakers[speaker_name] = speakers[speaker_name]
        else:
            missing_speakers.append(speaker_name)
    
    if missing_speakers:
        available = list(speakers.keys())
        raise Exception(f"Target speakers not found: {missing_speakers}. Available: {available}")
    
    return target_speakers

def get_speaker_state(speaker):
    """Get current state of a speaker (playing status and group info)"""
    coordinator = speaker.group.coordinator
    transport_state = coordinator.get_current_transport_info()["current_transport_state"]
    
    return {
        'speaker': speaker,
        'coordinator': coordinator,
        'is_playing': transport_state == "PLAYING",
        'group_members': list(coordinator.group.members),
        'is_coordinator': speaker == coordinator
    }

def get_group_snapshot(coordinator):
    """Take a group snapshot (playback + group state)"""
    snapshot = Snapshot(coordinator)
    snapshot.snapshot()
    return snapshot

def are_speakers_grouped_together(speaker1_state, speaker2_state):
    """Check if two speakers are grouped together"""
    return speaker1_state['coordinator'] == speaker2_state['coordinator']

def group_speakers_together(speaker1, speaker2):
    """Group two speakers together, making speaker1 the coordinator"""
    try:
        speaker2.join(speaker1)
        log(f"INFO: Grouped {speaker2.player_name} with {speaker1.player_name}")
        time.sleep(2)  # Allow time for group to form
        return speaker1.group.coordinator
    except Exception as e:
        raise Exception(f"Failed to group speakers: {e}")

def ungroup_speaker(speaker):
    """Remove speaker from its group"""
    try:
        speaker.unjoin()
        log(f"INFO: Ungrouped {speaker.player_name}")
        time.sleep(1)  # Allow time for group change
    except Exception as e:
        log(f"WARNING: Failed to ungroup {speaker.player_name}: {e}")

def play_mp3_on_group(coordinator, audio_url):
    """Play MP3 on the group coordinator"""
    try:
        coordinator.stop()
        coordinator.volume = VOLUME
        coordinator.play_uri(audio_url)
        log(f"SUCCESS: Played {audio_url} on group led by {coordinator.player_name}")
        
        duration = get_mp3_duration(audio_url)
        log(f"INFO: Waiting {duration} seconds for playback to finish")
        time.sleep(duration)
        
    except Exception as e:
        raise Exception(f"Failed to play MP3: {e}")

def restore_group_structure(group_snapshots):
    """Restore original group structure from snapshots"""
    try:
        for snapshot in group_snapshots:
            snapshot.restore()
        log("INFO: Restored original group and playback state")
    except Exception as e:
        log(f"WARNING: Failed to restore group structure: {e}")

def handle_scenario_1(speaker1, speaker2):
    """Scenario 1: Both speakers idle and not grouped"""
    log("INFO: Scenario 1 - Both speakers idle and not grouped")
    
    # Group speakers together
    coordinator = group_speakers_together(speaker1, speaker2)
    
    # Play MP3
    play_mp3_on_group(coordinator, AUDIO_URL)
    
    # Ungroup speakers
    ungroup_speaker(speaker2)
    
    log("INFO: Scenario 1 completed - speakers ungrouped")

def handle_scenario_2(speaker1_state, speaker2_state):
    """Scenario 2: Both speakers idle but in different groups"""
    log("INFO: Scenario 2 - Both speakers idle but in groups")
    
    # Save group snapshots (group state only since they're idle)
    snapshots = []
    if speaker1_state['coordinator'] != speaker1_state['speaker']:
        snapshots.append(get_group_snapshot(speaker1_state['coordinator']))
    if speaker2_state['coordinator'] != speaker2_state['speaker']:
        snapshots.append(get_group_snapshot(speaker2_state['coordinator']))
    
    # Remove from current groups and group together
    ungroup_speaker(speaker1_state['speaker'])
    ungroup_speaker(speaker2_state['speaker'])
    coordinator = group_speakers_together(speaker1_state['speaker'], speaker2_state['speaker'])
    
    # Play MP3
    play_mp3_on_group(coordinator, AUDIO_URL)
    
    # Restore original group structure
    restore_group_structure(snapshots)
    
    log("INFO: Scenario 2 completed - original groups restored")

def handle_scenario_3(speaker1_state, speaker2_state):
    """Scenario 3: One or both speakers are playing"""
    log("INFO: Scenario 3 - One or both speakers are playing")
    
    # Take full snapshots for all involved coordinators
    snapshots = []
    coordinators = set()
    coordinators.add(speaker1_state['coordinator'])
    coordinators.add(speaker2_state['coordinator'])
    
    for coordinator in coordinators:
        snapshots.append(get_group_snapshot(coordinator))
    
    # Remove from current groups and group together
    ungroup_speaker(speaker1_state['speaker'])
    ungroup_speaker(speaker2_state['speaker'])
    coordinator = group_speakers_together(speaker1_state['speaker'], speaker2_state['speaker'])
    
    # Play MP3
    play_mp3_on_group(coordinator, AUDIO_URL)
    
    # Restore snapshots
    restore_group_structure(snapshots)
    
    log("INFO: Scenario 3 completed - playback and groups restored")

def handle_scenario_4(coordinator):
    """Scenario 4: Already grouped together and idle"""
    log("INFO: Scenario 4 - Already grouped together and idle")
    
    # Just play MP3
    play_mp3_on_group(coordinator, AUDIO_URL)
    
    log("INFO: Scenario 4 completed - no state changes needed")

def handle_scenario_5(coordinator):
    """Scenario 5: Already grouped together and playing"""
    log("INFO: Scenario 5 - Already grouped together and playing")
    
    # Take snapshot
    snapshot = get_group_snapshot(coordinator)
    
    # Play MP3
    play_mp3_on_group(coordinator, AUDIO_URL)
    
    # Restore snapshot
    restore_group_structure([snapshot])
    
    log("INFO: Scenario 5 completed - previous playback restored")

def handle_scenario_6(speaker1_state, speaker2_state):
    """Scenario 6: Grouped with other speakers not involved"""
    log("INFO: Scenario 6 - Speakers grouped with others")
    
    # Save group snapshots for all involved groups
    snapshots = []
    coordinators = set()
    coordinators.add(speaker1_state['coordinator'])
    coordinators.add(speaker2_state['coordinator'])
    
    for coordinator in coordinators:
        snapshots.append(get_group_snapshot(coordinator))
    
    # Remove target speakers from their groups and group together
    ungroup_speaker(speaker1_state['speaker'])
    ungroup_speaker(speaker2_state['speaker'])
    coordinator = group_speakers_together(speaker1_state['speaker'], speaker2_state['speaker'])
    
    # Play MP3
    play_mp3_on_group(coordinator, AUDIO_URL)
    
    # Restore original group structure
    restore_group_structure(snapshots)
    
    log("INFO: Scenario 6 completed - original groups with others restored")

def main():
    """Main execution logic"""
    try:
        log(f"INFO: Starting group play for {AUDIO_URL}")
        
        # Discover and find target speakers
        all_speakers = discover_speakers()
        target_speakers = find_target_speakers(all_speakers)
        
        speaker1_name, speaker2_name = GROUP_SPEAKERS
        speaker1 = target_speakers[speaker1_name]
        speaker2 = target_speakers[speaker2_name]
        
        # Get current state of both speakers
        speaker1_state = get_speaker_state(speaker1)
        speaker2_state = get_speaker_state(speaker2)
        
        log(f"INFO: {speaker1_name} - Playing: {speaker1_state['is_playing']}, Group: {speaker1_state['coordinator'].player_name}")
        log(f"INFO: {speaker2_name} - Playing: {speaker2_state['is_playing']}, Group: {speaker2_state['coordinator'].player_name}")
        
        # Determine scenario and handle accordingly
        both_idle = not speaker1_state['is_playing'] and not speaker2_state['is_playing']
        grouped_together = are_speakers_grouped_together(speaker1_state, speaker2_state)
        
        if grouped_together:
            if both_idle:
                # Scenario 4: Already grouped together and idle
                handle_scenario_4(speaker1_state['coordinator'])
            else:
                # Scenario 5: Already grouped together and playing
                handle_scenario_5(speaker1_state['coordinator'])
        else:
            if both_idle:
                # Check if they're in groups with others
                speaker1_in_group = len(speaker1_state['group_members']) > 1
                speaker2_in_group = len(speaker2_state['group_members']) > 1
                
                if not speaker1_in_group and not speaker2_in_group:
                    # Scenario 1: Both idle and not grouped
                    handle_scenario_1(speaker1, speaker2)
                else:
                    # Scenario 2: Both idle but in groups
                    handle_scenario_2(speaker1_state, speaker2_state)
            else:
                # One or both are playing
                # Check if they have other group members
                speaker1_has_others = len(speaker1_state['group_members']) > 1 and not grouped_together
                speaker2_has_others = len(speaker2_state['group_members']) > 1 and not grouped_together
                
                if speaker1_has_others or speaker2_has_others:
                    # Scenario 6: Grouped with others
                    handle_scenario_6(speaker1_state, speaker2_state)
                else:
                    # Scenario 3: Playing but not with others
                    handle_scenario_3(speaker1_state, speaker2_state)
        
        log(f"SUCCESS: Group play completed for {AUDIO_URL}")
        
    except Exception as e:
        log(f"ERROR: Group play failed - {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()