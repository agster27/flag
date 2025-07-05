# Sonos Scheduled Playback: Colors & Taps Automation

This project automates the daily playback of "Colors" at 8:00 AM and "Taps" at sunset using a Sonos speaker system and a virtual server or container.

## Features

- Play `colors.mp3` daily at 8:00 AM
- Play `taps.mp3` daily at sunset (automatically calculated)
- Pauses current playback and restores original audio after scheduled track
- Skips restore if Sonos speaker was idle
- Logs all playback events to `/opt/sonos_play.log`
- Serves audio files over local HTTP
- All variables (Sonos IP, volume, MP3 URLs) are configurable via `config.json`

## System Requirements

- Python 3.8+
- Sonos speaker (reachable on the same subnet)
- Debian/Ubuntu VM or LXC (Proxmox-compatible)
- Python virtual environment
- `colors.mp3` and `taps.mp3` stored in `/opt/audio/`

## Dependencies

Install system-level dependencies:

```bash
sudo apt update
sudo apt install python3-full python3-venv ffmpeg jq -y
```

Create Python virtual environment and install packages:

```bash
cd /opt
python3 -m venv sonos-env
source sonos-env/bin/activate
pip install soco astral pytz mutagen
```

## Project Structure

```
/opt/
├── sonos_play.py          # Main playback handler
├── sunset_timer.py        # Calculates today's sunset time
├── schedule_sonos.sh      # Updates daily cron job for sunset
├── sonos_play.log         # Log file (auto-created)
├── sonos-env/             # Python virtual environment
├── config.json            # User configuration (IP, volume, URLs)
└── audio/
    ├── colors.mp3         # 8:00 AM scheduled song
    └── taps.mp3           # Sunset scheduled song
```

## Serve MP3s via HTTP

This makes your audio files accessible to Sonos:

```bash
python3 -m http.server 8000 --directory /opt --bind 0.0.0.0
```

### Auto-start HTTP on boot (optional)

Create a service file:

```bash
sudo nano /etc/systemd/system/audio-server.service
```

Paste:

```
[Unit]
Description=Audio HTTP Server
After=network.target

[Service]
ExecStart=/usr/bin/python3 -m http.server 8000 --directory /opt --bind 0.0.0.0
WorkingDirectory=/opt
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now audio-server
```

## Configure with config.json

Create or edit `/opt/config.json`:

```json
{
  "sonos_ip": "192.168.1.50",
  "volume": 30,
  "colors_url": "http://flag.aghy.home:8000/audio/colors.mp3",
  "taps_url": "http://flag.aghy.home:8000/audio/taps.mp3",
  "default_wait_seconds": 60,
  "skip_restore_if_idle": true
}
```

## Cron Setup

Edit the root crontab:

```bash
sudo crontab -e
```

Add:

```
# Play Colors at 8 AM
0 8 * * * /opt/sonos-env/bin/python /opt/sonos_play.py http://flag.aghy.home:8000/audio/colors.mp3

# Update sunset schedule daily at 2 AM
0 2 * * * /opt/schedule_sonos.sh
```

`schedule_sonos.sh` will use `config.json` and dynamically create the proper `taps.mp3` schedule based on sunset.

## Testing

To manually test playback:

```bash
/opt/sonos-env/bin/python /opt/sonos_play.py http://flag.aghy.home:8000/audio/colors.mp3
```

Check logs:

```bash
tail -n 10 /opt/sonos_play.log
```

Expected output if something was playing before:

```
INFO: Took snapshot of Living Room (was_playing=True)
SUCCESS: Played http://flag.aghy.home:8000/audio/colors.mp3 on Living Room
INFO: Waiting 52 seconds for playback to finish
INFO: Restored previous playback on Living Room
```

If idle before:

```
INFO: Took snapshot of Living Room (was_playing=False)
SUCCESS: Played http://flag.aghy.home:8000/audio/taps.mp3 on Living Room
INFO: Waiting 42 seconds for playback to finish
INFO: No prior playback. Skipping restore.
```

## License

MIT — use freely for personal, civic, or ceremonial purposes.

## Author

Michael Aghajanian  
GitHub: [@agster27](https://github.com/agster27)
