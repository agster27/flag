# Honor tradition with tech

🎖️ **Honor tradition with tech** — This project plays the bugle calls **Colors** at 8:00 AM and **Taps** at sunset on a Sonos speaker automatically every day.

---

## 🌟 Features

✅ Play `colors.mp3` at **0800 sharp** every morning  
🌅 Dynamically calculate **sunset time** to play `taps.mp3`  
🔇 Pause what's playing and **restore** it after the call  
📄 Log every playback to `/opt/flag/sonos_play.log`  
📡 Serve your MP3s via a **tiny HTTP server**  
⚙️ Customize everything via `/opt/flag/config.json`  

---

## 🧰 Requirements

- 🐍 Python 3.8+
- 📶 Sonos speaker on the local network
- 🖥️ Ubuntu/Debian VM or LXC container (Proxmox-ready)
- 🎧 Your own `colors.mp3` and `taps.mp3` in `/opt/flag/audio/`

---

## 🚀 Easy Setup

**Run setup.sh from a directory OUTSIDE `/opt/flag` (for example, `/root` or `/opt`):**

```bash
cd /opt
wget https://raw.githubusercontent.com/agster27/flag/main/setup.sh -O setup.sh
chmod +x setup.sh
./setup.sh
```

This will:

- Install all dependencies
- Set up your virtual environment
- Clone this GitHub repo into `/opt/flag`
- Copy over the Python scripts
- Set up a sample `/opt/flag/config.json`
- Always force your local copy to match GitHub (any local changes in `/opt/flag` will be lost)

---

## 🔧 Manual Setup (if you're hardcore)

```bash
sudo apt update
sudo apt install python3-full python3-venv ffmpeg jq git -y
cd /opt/flag
python3 -m venv sonos-env
source sonos-env/bin/activate
pip install soco astral pytz mutagen
```

---

## 🗂️ Project Layout

```
/opt/flag/
├── sonos_play.py          # Plays the MP3
├── sunset_timer.py        # Calculates sunset
├── schedule_sonos.sh      # Adds dynamic sunset cron
├── sonos_play.log         # 🎯 Log file
├── config.json            # 🔧 Settings
├── sonos-env/             # 🐍 Virtual environment
└── audio/
    ├── colors.mp3         # 🎶 Morning bugle call
    └── taps.mp3           # 🌅 Evening taps
```

---

## 📡 MP3 Hosting

```bash
python3 -m http.server 8000 --directory /opt/flag --bind 0.0.0.0
```

💡 Tip: Set it to auto-start with a `systemd` service!

---

## 📝 Config

Edit `/opt/flag/config.json` to match your Sonos and preferences:

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

---

## ⏰ Cron Setup

Edit the crontab with:

```bash
sudo crontab -e
```

Add these jobs:

```cron
# Colors at 8:00 AM
0 8 * * * /opt/flag/sonos-env/bin/python /opt/flag/sonos_play.py http://flag.aghy.home:8000/audio/colors.mp3

# Sunset schedule update at 2:00 AM
0 2 * * * /opt/flag/schedule_sonos.sh
```

---

## 🧪 Testing

Run manually:

```bash
/opt/flag/sonos-env/bin/python /opt/flag/sonos_play.py http://flag.aghy.home:8000/audio/colors.mp3
```

Check the log:

```bash
tail -n 10 /opt/flag/sonos_play.log
```

---

## 📜 License

MIT — use freely for civic, personal, or ceremonial purposes.

---

## ✍️ Author

🫡 Created by  
Michael Aghajanian — Marine, civic leader, and builder of better systems.

GitHub: [@agster27](https://github.com/agster27)
