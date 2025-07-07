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

**Download and run the setup script from any directory (e.g., `/root` or `/opt`):**

```bash
wget --no-cache https://raw.githubusercontent.com/agster27/flag/main/setup.sh -O setup.sh
chmod +x setup.sh
./setup.sh
```

**You will be prompted with:**
1. Update/install the latest scripts (recommended for first install or upgrades)
2. Uninstall completely (removes all files and cron jobs)
3. Exit without doing anything

> The script will automatically download all required files from GitHub using wget (no `git clone` needed), create a Python virtual environment, install dependencies, and generate a default `config.json` if needed.

---

## 🗂️ Project Layout

After setup, your `/opt/flag/` folder should look like:

```
/opt/flag/
├── sonos_play.py          # Plays the MP3
├── sunset_timer.py        # Calculates sunset
├── schedule_sonos.sh      # Adds dynamic sunset cron
├── audio_check.py         # Audio check script
├── README.md              # Project readme (downloaded for reference)
├── LICENSE                # Project license (downloaded for reference)
├── requirements.txt       # Python requirements (downloaded for reference)
├── sonos_play.log         # 🎯 Log file (created at runtime)
├── config.json            # 🔧 Settings (auto-generated if missing)
├── sonos-env/             # 🐍 Virtual environment
└── audio/
    ├── colors.mp3         # 🎶 Morning bugle call (add your own)
    └── taps.mp3           # 🌅 Evening taps (add your own)
```

---

## 📡 MP3 Hosting

A systemd-managed HTTP server is set up to serve your audio files directly from `/opt/flag/audio/`.  
You do **not** need to run `git clone` or start the server manually.

Your files will be available at:

- [http://flag.aghy.home:8000/colors.mp3](http://flag.aghy.home:8000/colors.mp3)
- [http://flag.aghy.home:8000/taps.mp3](http://flag.aghy.home:8000/taps.mp3)

Check the server status or restart it with:

```bash
sudo systemctl status flag-audio-http
sudo systemctl restart flag-audio-http
```

---

## 📝 Config

Edit `/opt/flag/config.json` to match your Sonos and preferences:

```json
{
  "volume": 30,
  "colors_url": "http://flag.aghy.home:8000/colors.mp3",
  "taps_url": "http://flag.aghy.home:8000/taps.mp3",
  "default_wait_seconds": 60,
  "skip_restore_if_idle": true,
  "latitude": 42.1,
  "longitude": -71.5,
  "timezone": "America/New_York",
  "group_speakers": ["sonos-flag", "sonos-backyard"]
}
```

### 🔊 Speaker Configuration

The `group_speakers` field specifies which Sonos speakers to use for playback. This unified approach works for both single speakers and groups:

- **Single speaker**: `"group_speakers": ["living-room"]`
- **Multiple speakers**: `"group_speakers": ["living-room", "kitchen", "bedroom"]`

Speaker names should match the names shown in your Sonos app. The system will:
1. Automatically discover all Sonos speakers on your network
2. Group the specified speakers together for playback (if multiple)
3. Play the audio on the group
4. Restore the previous state after playback

This approach ensures consistent behavior whether you're using one speaker or many, simplifying configuration and maintenance.
---

## 🧪 Testing

After setup, you should test that all components work:

### 1. Test Audio HTTP Server

Check if your audio files are served correctly:

```bash
curl -I http://localhost:8000/colors.mp3
curl -I http://localhost:8000/taps.mp3
```

You should see `HTTP/1.0 200 OK` in the response headers.
You can also test in your browser: [http://flag.aghy.home:8000/colors.mp3](http://flag.aghy.home:8000/colors.mp3)

### 2. Test Sonos Playback Manually

To test playbook without waiting for the scheduled time, run:

```bash
/opt/flag/sonos-env/bin/python /opt/flag/sonos_play.py http://flag.aghy.home:8000/colors.mp3
```

or, for taps:

```bash
/opt/flag/sonos-env/bin/python /opt/flag/sonos_play.py http://flag.aghy.home:8000/taps.mp3
```

The script will automatically:
- Discover all Sonos speakers on your network
- Find the speakers specified in your `group_speakers` configuration  
- Group them together (if multiple speakers)
- Play the audio on the group
- Restore the previous playback state

If it works, you'll hear the audio play on your configured speakers and see log output in `/opt/flag/sonos_play.log`.

### 3. Test Scheduling

- Check that the cron jobs are installed:

  ```bash
  crontab -l
  ```

- You should see entries for the morning and sunset calls.

- To test scheduling, you can temporarily edit the crontab to run a minute in the future and observe playback.

### 4. Check Logs

Review the log file for any errors or confirmations:

```bash
cat /opt/flag/sonos_play.log
```

---

## 🛠️ Troubleshooting

- **Check audio server:**  
  `sudo systemctl status flag-audio-http`
- **Check logs:**  
  `cat /opt/flag/sonos_play.log`
- **Check crontab:**  
  `crontab -l`
- **Test playback manually:**  
  See the section above on manual testing.

---

## 🙏 Credits

Created by agster27.  
Inspired by tradition, powered by Python and Sonos.

---
