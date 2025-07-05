# 🇺🇸 Flag Sonos Scheduler

A robust, self-hosted system to automatically play "colors.mp3" and "taps.mp3" on your Sonos speaker at scheduled times (e.g., morning and sunset).

---

## 🌟 Features

✅ Play `colors.mp3` at **0800 sharp** every morning  
🌅 Dynamically calculate **sunset time** to play `taps.mp3`  
🔇 Pause what's playing and **restore** it after the call  
📄 Log every playback to `/opt/flag/sonos_play.log`  
📡 Serve your MP3s via a **tiny HTTP server** (systemd-managed)  
⚙️ Customize everything via `/opt/flag/config.json`  

---

## 🧰 Requirements

- 🐍 Python 3.8+
- 📶 Sonos speaker on the local network
- 🖥️ Ubuntu/Debian VM or LXC container (Proxmox-ready)
- 🎧 Your own `colors.mp3` and `taps.mp3` in `/opt/flag/audio/`

---

## 🚀 Installation

Clone the repo and run the setup script:

```bash
git clone https://github.com/agster27/flag.git /opt/flag
cd /opt/flag
chmod +x setup.sh
./setup.sh
```

- This will:
  - Download required files and audio.
  - Set up a Python virtual environment with dependencies.
  - Configure a systemd service to serve your audio files.
  - Set up scheduled Sonos playback (via cron).

---

## 📡 MP3 Hosting

**Audio files are automatically served via a systemd-managed Python HTTP server.**

You do **not** need to manually run `python3 -m http.server`—the systemd service handles this for you.

The server is configured to serve files from `/opt/flag/audio` at [http://flag.aghy.home:8000/](http://flag.aghy.home:8000/).

To play or download audio directly, use:

- [http://flag.aghy.home:8000/colors.mp3](http://flag.aghy.home:8000/colors.mp3)
- [http://flag.aghy.home:8000/taps.mp3](http://flag.aghy.home:8000/taps.mp3)

If you need to check the server status or restart it, use:

```bash
sudo systemctl status flag-audio-http
sudo systemctl restart flag-audio-http
```

---

## 📝 Config

Edit `/opt/flag/config.json` to match your Sonos and preferences:

```json
{
  "sonos_ip": "192.168.1.50",
  "volume": 30,
  "colors_url": "http://flag.aghy.home:8000/colors.mp3",
  "taps_url": "http://flag.aghy.home:8000/taps.mp3",
  "default_wait_seconds": 60,
  "skip_restore_if_idle": true,
  "latitude": 42.1,
  "longitude": -71.5,
  "timezone": "America/New_York"
}
```

---

## 🔄 Updating Audio

To update your audio files, simply replace the MP3s in `/opt/flag/audio/`.  
The HTTP server and Sonos scripts will automatically use the new files.

---

## 🔔 Scheduling Details

- `colors.mp3` is played at 08:00 every day.
- `taps.mp3` is played at **sunset** (calculated for your configured location).
- Cron jobs are managed automatically.
- All playback and errors are logged to `/opt/flag/sonos_play.log`.

---

## 🛠️ Troubleshooting

- **Check audio server:**  
  `sudo systemctl status flag-audio-http`
- **Check logs:**  
  `cat /opt/flag/sonos_play.log`
- **Check crontab:**  
  `crontab -l`
- **Test playback manually:**  
  ```bash
  /opt/flag/sonos-env/bin/python /opt/flag/sonos_play.py http://flag.aghy.home:8000/colors.mp3
  ```

---

## 🙏 Credits

Created by agster27.  
Inspired by tradition, powered by Python and Sonos.

---
