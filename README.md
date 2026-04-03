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
🖥️ Scheduled via **systemd timers** — better logging, auto-retry, and `Persistent=true` boot resilience (critical for Raspberry Pi)  
🎵 **Extensible schedules** — add any number of scheduled plays by editing `config.json`, no code changes needed  

---

## 🧰 Requirements

- 🐍 Python 3.8+
- 📶 Sonos speaker on the local network
- 🖥️ Ubuntu/Debian VM, LXC container, or **Raspberry Pi** (systemd required)
- 🎧 Default `colors.mp3` and `taps.mp3` audio files are included; replace with your own if desired

---

## 🚀 Easy Setup

**Download and run the setup script from any directory (e.g., `/root` or `/opt`):**

```bash
wget --no-cache https://raw.githubusercontent.com/agster27/flag/main/setup.sh -O setup.sh
chmod +x setup.sh
./setup.sh
```

**You will be prompted with a menu:**

```
╔══════════════════════════════════════════╗
║     Honor Tradition with Tech — Setup    ║
║     Version 2.1.0                        ║
║     Status: ✅ Installed                  ║
╚══════════════════════════════════════════╝
  Config: Sonos IP: 192.168.1.50 | 2 schedule(s) | Volume: 30

  ── Read-only ──────────────────────────
  1) List scheduled plays
  2) Test Sonos playback
  3) View logs

  ── Configuration ──────────────────────
  4) Install (first-time setup)
  5) Upgrade (update scripts, keep config)
  6) Reconfigure (edit config.json interactively)

  ── Danger zone ────────────────────────
  7) Uninstall completely

  8) Exit without doing anything
```

> **Install state detection:** When `setup.sh` loads, it automatically checks for the Python virtual environment (`/opt/flag/sonos-env`), the config file (`/opt/flag/config.json`), and active systemd timers. If any component is missing, a warning is displayed above the menu with guidance on which option to select. On a fresh system, the "Install" option is marked with `← start here` and options that require a working installation are annotated with `(requires install)`.

| Option | Action |
|--------|--------|
| **1** | List scheduled plays — shows all configured schedules, systemd timer status, and audio HTTP server status |
| **2** | Test Sonos playback — plays a test audio clip on your Sonos speaker |
| **3** | View logs — shows the last 20 lines of `setup.log` and `sonos_play.log` |
| **4** | Install (first-time setup) — installs system deps, downloads files, creates venv, runs config wizard, writes systemd timers |
| **5** | Upgrade — downloads latest scripts from GitHub and upgrades pip packages; **preserves your existing `config.json`** |
| **6** | Reconfigure — re-runs the config wizard to edit settings and regenerate timers |
| **7** | Uninstall — removes all files, systemd services, and timers |
| **8** | Exit without making any changes |

> The script will automatically download all required files from GitHub using wget (no `git clone` needed), create a Python virtual environment, install dependencies, and generate a default `config.json` if needed.

---

## 🗂️ Project Layout

After setup, your `/opt/flag/` folder should look like:

```
/opt/flag/
├── sonos_play.py          # Plays the MP3 on Sonos
├── schedule_sonos.py      # Calculates sunset and writes systemd timer unit files
├── audio_check.py         # Validates and converts audio files
├── config.py              # Central configuration loader
├── README.md              # Project readme (downloaded for reference)
├── requirements.txt       # Python requirements (downloaded for reference)
├── sonos_play.log         # 🎯 Playback log file (created at runtime)
├── setup.log              # 🔧 Setup log file (created by setup.sh)
├── config.json            # 🔧 Settings (auto-generated if missing)
├── sonos-env/             # 🐍 Virtual environment
└── audio/
    ├── colors.mp3         # 🎶 Morning bugle call (default included; replace with your own)
    └── taps.mp3           # 🌅 Evening taps (default included; replace with your own)
```

**Systemd unit files** (written by `schedule_sonos.py` to `/etc/systemd/system/`):

```
flag-colors.service / flag-colors.timer       # Colors at 08:00
flag-taps.service   / flag-taps.timer         # Taps at sunset (updated daily)
flag-reschedule.service / flag-reschedule.timer  # Daily 02:00 — recalculates sunset
flag-audio-http.service                       # HTTP audio file server
```

---

## 📡 MP3 Hosting

A systemd-managed HTTP server is set up to serve your audio files directly from `/opt/flag/audio/`.  
You do **not** need to run `git clone` or start the server manually.

Your files will be available at:

- [http://<your-pi-ip>:8000/colors.mp3](http://<your-pi-ip>:8000/colors.mp3)
- [http://<your-pi-ip>:8000/taps.mp3](http://<your-pi-ip>:8000/taps.mp3)

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
  "sonos_ip": "192.168.1.50",
  "port": 8000,
  "volume": 30,
  "default_wait_seconds": 60,
  "skip_restore_if_idle": true,
  "latitude": 42.1,
  "longitude": -71.5,
  "timezone": "America/New_York",
  "sunset_offset_minutes": 0,
  "schedules": [
    {
      "name": "colors",
      "audio_url": "http://192.168.1.10:8000/colors.mp3",
      "time": "08:00"
    },
    {
      "name": "taps",
      "audio_url": "http://192.168.1.10:8000/taps.mp3",
      "time": "sunset"
    }
  ]
}
```

### Top-level keys

| Key | Description |
|-----|-------------|
| `sonos_ip` | IP address of your Sonos speaker |
| `port` | Port the HTTP audio server listens on (default: `8000`) |
| `volume` | Playback volume (0–100) |
| `default_wait_seconds` | Fallback wait time (seconds) if MP3 duration cannot be determined |
| `skip_restore_if_idle` | If `true`, do not restore prior playback when speaker was idle |
| `latitude` / `longitude` | Your coordinates, used to calculate local sunset time |
| `timezone` | IANA timezone name (e.g. `"America/New_York"`) |
| `sunset_offset_minutes` | Optional offset in minutes from sunset (negative = before, positive = after). Defaults to `0` |

### `schedules` array

Each entry in `schedules` defines one scheduled audio play:

| Field | Description |
|-------|-------------|
| `name` | Unique name used as the systemd unit suffix (`flag-{name}.service` / `flag-{name}.timer`). Must contain only letters, numbers, hyphens, and underscores. |
| `audio_url` | Full HTTP URL of the MP3 to play (served by the built-in audio HTTP server). |
| `time` | When to play: either `"HH:MM"` (24-hour local time) or the special value `"sunset"`. |

> **Backward compatibility:** If you have an older install that still uses the flat `colors_url` / `taps_url` / `colors_time` keys, `schedule_sonos.py` will automatically synthesise a schedules list from them and print a deprecation warning. Re-run `setup.sh` → option 6 (Reconfigure) to permanently migrate to the new format.

---

## ➕ Adding a New Scheduled Play

To add a new scheduled audio play (e.g., a 17:00 retreat call):

1. **Add an audio file** to `/opt/flag/audio/` (e.g., `retreat.mp3`)

2. **Edit `/opt/flag/config.json`** and add an entry to the `schedules` array:

   ```json
   {
     "name": "retreat",
     "audio_url": "http://192.168.1.10:8000/retreat.mp3",
     "time": "17:00"
   }
   ```

3. **Re-run setup.sh** and choose option **6 (Reconfigure)**, or run:

   ```bash
   sudo /opt/flag/sonos-env/bin/python /opt/flag/schedule_sonos.py
   ```

4. Verify the new timer is active:

   ```bash
   systemctl list-timers --all | grep flag
   ```

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

### 2. Test Sonos Playback Manually

To test playback without waiting for the scheduled time, run:

```bash
/opt/flag/sonos-env/bin/python /opt/flag/sonos_play.py http://<your-pi-ip>:8000/colors.mp3
```

or, for taps:

```bash
/opt/flag/sonos-env/bin/python /opt/flag/sonos_play.py http://<your-pi-ip>:8000/taps.mp3
```

If it works, you'll hear the audio play on your Sonos and see log output in `/opt/flag/sonos_play.log`.

### 3. Check Installed Timers

Verify all timers were installed and show their next fire times:

```bash
systemctl list-timers --all | grep flag
```

You should see entries for each schedule (`flag-colors`, `flag-taps`) and the daily reschedule (`flag-reschedule`).

### 4. View Timer Logs

Check journal logs for a specific timer/service:

```bash
journalctl -u flag-colors -n 50
journalctl -u flag-taps -n 50
journalctl -u flag-reschedule -n 20
```

### 5. Check General Logs

Review the playback log file for errors or confirmations:

```bash
cat /opt/flag/sonos_play.log
```

The setup log (written by `setup.sh`) is at:

```bash
cat /opt/flag/setup.log
```

---

## 🛠️ Troubleshooting

- **Check audio server:**  
  `sudo systemctl status flag-audio-http`
- **Check a specific timer status:**  
  `systemctl status flag-colors.timer`  
  `systemctl status flag-taps.timer`
- **Check logs for a service:**  
  `journalctl -u flag-colors -n 50`  
  `journalctl -u flag-taps -n 50`
- **List all flag timers and their next fire time:**  
  `systemctl list-timers --all | grep flag`
- **Check playback log:**  
  `cat /opt/flag/sonos_play.log`
- **Check setup log:**  
  `cat /opt/flag/setup.log`
- **Manually trigger a play (for testing):**  
  `sudo systemctl start flag-colors.service`
- **Sunset timer shows the wrong time?**  
  The `flag-reschedule` timer recalculates sunset at 02:00 each night. To recalculate immediately:  
  `sudo /opt/flag/sonos-env/bin/python /opt/flag/schedule_sonos.py`
- **Why doesn't the reschedule restart the sunset timer?**  
  By design, the nightly 02:00 reschedule run stops sunset timers (e.g. `flag-taps.timer`) before rewriting their unit files, then leaves them stopped after `daemon-reload`. Starting (or restarting) a sunset timer at 02:00 causes systemd to invoke the associated service immediately at 02:00 — an unwanted early-morning audio play. The updated `OnCalendar` line is already written to disk and loaded by `daemon-reload`; systemd will activate the timer at the correct sunset time without an explicit `systemctl start`. If you ever need to immediately activate the sunset timer manually, run:  
  `sudo systemctl start flag-taps.timer`

---

## 🙏 Credits

Created by agster27.  
Inspired by tradition, powered by Python and Sonos.

---
