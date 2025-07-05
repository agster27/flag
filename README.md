# Sonos Scheduled Playback: Colors & Taps Automation

This project automates the daily playback of "Colors" at 8:00 AM and "Taps" at sunset using a Sonos speaker system and a virtual server or container.

## Features

- Play `colors.mp3` daily at 8:00 AM
- Play `taps.mp3` daily at sunset (automatically calculated)
- Pauses current playback and restores original audio after scheduled track
- Skips restore if Sonos speaker was idle
- Logs all playback events to `/opt/sonos_play.log`
- Serves audio files over local HTTP

## System Requirements

- Python 3.8+
- Sonos speaker (reachable on the same subnet)
- Debian/Ubuntu VM or LXC (Proxmox-compatible)
- Python virtual environment
- `colors.mp3` and `taps.mp3` stored in `/opt/audio/`

## 📁 Project Structure

```
/opt/
├── sonos_play.py          # Main playback handler
├── sunset_timer.py        # Calculates today's sunset time
├── schedule_sonos.sh      # Updates daily cron job for sunset
├── sonos_play.log         # Log file (auto-created)
├── sonos-env/             # Python virtual environment
└── audio/
    ├── colors.mp3         # 8:00 AM scheduled song
    └── taps.mp3           # Sunset scheduled song
```



---

## ⚙️ Setup Instructions

1. **Install Python dependencies in virtual env**:
    ```bash
    sudo apt install python3-full python3-venv ffmpeg
    cd /opt
    python3 -m venv sonos-env
    source sonos-env/bin/activate
    pip install soco astral pytz mutagen
    ```

2. **Host audio files via HTTP**:
    ```bash
    mkdir -p /opt/audio
    cp colors.mp3 taps.mp3 /opt/audio/
    python3 -m http.server 8000 --directory /opt/audio --bind 0.0.0.0
    ```

3. **Enable auto-start of audio server (optional)**:
    Create `/etc/systemd/system/audio-server.service` with:
    ```ini
    [Unit]
    Description=Audio HTTP Server
    After=network.target

    [Service]
    ExecStart=/usr/bin/python3 -m http.server 8000 --directory /opt/audio --bind 0.0.0.0
    WorkingDirectory=/opt/audio
    Restart=always

    [Install]
    WantedBy=multi-user.target
    ```

    Then:
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable --now audio-server
    ```

4. **Edit and configure your crontab**:
    ```bash
    sudo crontab -e
    ```

    Add:
    ```cron
    0 8 * * * /opt/sonos-env/bin/python /opt/sonos_play.py http://flag.aghy.home:8000/audio/colors.mp3
    0 2 * * * /opt/schedule_sonos.sh
    ```

---

## 🧪 Testing

- Play something on Sonos manually
- Run this to test:
    ```bash
    /opt/sonos-env/bin/python /opt/sonos_play.py http://flag.aghy.home:8000/audio/colors.mp3
    ```

- Log file:
    ```bash
    tail -n 10 /opt/sonos_play.log
    ```

---

## 📌 Notes

- Uses `mutagen` to dynamically determine MP3 length
- Avoids restoring if speaker was idle before playback
- Requires proper HTTP hosting (no redirects)
- Be sure to use the **group coordinator** Sonos IP

---

## 📄 License

MIT — you are free to use and adapt this for home, civic, or ceremonial use.

---

## 🫡 Credit

Created with care and respect by [Michael Aghajanian](https://github.com/agster27) for ceremonial precision, automated honor, and clean code.

