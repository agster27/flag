# Honor tradition with tech

🎖️ **Honor tradition with tech** — This project plays the bugle calls **Colors** at 8:00 AM and **Taps** at sunset on one or more Sonos speakers automatically every day.

---

## 🌟 Features

✅ Play `colors.mp3` at **0800 sharp** every morning  
🌅 Dynamically calculate **sunset time** to play `taps.mp3`  
🔊 **Multi-speaker synchronized playback** — configure one or more Sonos speakers; all play in sync via a temporary Sonos group  
🔇 Pause what's playing and **restore** it after the call — per speaker, including volume  
📄 Log every playback to `/opt/flag/sonos_play.log`  
📡 Serve your MP3s via a **tiny HTTP server**  
⚙️ Customize everything via `/opt/flag/config.json`  
🖥️ Scheduled via **systemd timers** — better logging, structured journald output, and explicit "missed plays are skipped, never replayed late" semantics  
🎵 **Extensible schedules** — add any number of scheduled plays by editing `config.json`, no code changes needed  

---

## 🧰 Requirements

- 🐍 Python 3.8+
- 📶 One or more Sonos speakers on the local network
- 🖥️ Ubuntu/Debian VM, LXC container, or **Raspberry Pi** (systemd required)
- 🎧 Default `colors.mp3` and `taps.mp3` audio files are included; replace with your own if desired

---

---

## ⌨️ Command-line Usage

In addition to the interactive menu, `setup.sh` supports non-interactive CLI invocation:

```bash
# Show help / usage summary
./setup.sh --help

# Uninstall interactively (prompts for confirmation)
./setup.sh uninstall

# Uninstall without any prompt — useful for scripted or remote teardown
./setup.sh uninstall --yes
```

| Command | Description |
|---------|-------------|
| `./setup.sh` | Launch the interactive menu (default) |
| `./setup.sh uninstall` | Remove all files, systemd units, legacy dirs, and cron entries; prompts `[y/N]` |
| `./setup.sh uninstall --yes` | Same as above but skips the confirmation prompt |
| `./setup.sh --help` | Print this usage summary and exit |

Aliases: `--uninstall` and `-u` are accepted in place of `uninstall`; `-y` is accepted in place of `--yes`.

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
║     Version 2.2.0                        ║
║     Status: ✅ Installed                  ║
╚══════════════════════════════════════════╝
  Config:  Speakers (2): Living Room (192.168.1.50), Kitchen (192.168.1.51)
           Schedules: 2
  Sunset:  🌅 19:45 (America/New_York)

  ── Read-only ──────────────────────────
  1) List scheduled plays
  2) Show sunset time
  3) Test Sonos playback
  4) View logs

  ── Configuration ──────────────────────
  5) Install (first-time setup)
  6) Upgrade (update scripts, keep config)
  7) Reconfigure (edit config.json interactively)

  ── Danger zone ────────────────────────
  8) Uninstall completely

  9) Exit without doing anything
```

> **Install state detection:** When `setup.sh` loads, it automatically checks for the Python virtual environment (`/opt/flag/sonos-env`), the config file (`/opt/flag/config.json`), and active systemd timers. If any component is missing, a warning is displayed above the menu with guidance on which option to select. On a fresh system, the "Install" option is marked with `← start here` and options that require a working installation are annotated with `(requires install)`.

| Option | Action |
|--------|--------|
| **1** | List scheduled plays — shows all configured schedules, systemd timer status, and audio HTTP server status |
| **2** | Show sunset time — calculates today's sunset based on your configured coordinates and displays the time (with any configured offset) |
| **3** | Test Sonos playback — plays a test audio clip on your configured Sonos speaker(s) |
| **4** | View logs — shows the last 20 lines of `setup.log` and `sonos_play.log` |
| **5** | Install (first-time setup) — installs system deps, downloads files, creates venv, runs config wizard, writes systemd timers |
| **6** | Upgrade — downloads latest scripts from GitHub and upgrades pip packages; **preserves your existing `config.json`** |
| **7** | Reconfigure — re-runs the config wizard to edit settings and regenerate timers |
| **8** | Uninstall — removes all files, systemd services, and timers |
| **9** | Exit without making any changes |

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
flag-boot-reschedule.service                  # Oneshot on boot — recomputes sunset before timers fire
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
  "speakers": ["192.168.1.50", "192.168.1.51"],
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

### Multi-speaker synchronized playback

When `speakers` contains more than one IP address, all speakers play the bugle call in perfect sync using a temporary Sonos group:

1. Each speaker's current state (group membership, transport state, volume) is snapshotted.
2. All speakers are temporarily unjoined from their existing groups.
3. They join a temporary "bugle group" under the first speaker in the list (the coordinator).
4. The audio plays on the coordinator — Sonos keeps all members in sync automatically.
5. After playback, the temporary group is dissolved.
6. Each speaker rejoins its original group and the prior playback state is restored.

This means if Speaker A was playing Spotify before Colors, it will resume playing Spotify afterward. Speakers that were idle remain idle after the bugle call (when `skip_restore_if_idle=true`).

### Top-level keys

| Key | Description |
|-----|-------------|
| `speakers` | **Required.** Array of speaker entries (see below). All speakers play in synchronized playback. |
| `port` | Port the HTTP audio server listens on (default: `8000`) |
| `volume` | Default playback volume for the bugle call (0–100). Acts as the fallback when a speaker has no individual `volume`. Each speaker's original volume is restored afterward. |
| `default_wait_seconds` | Fallback wait time (seconds) if MP3 duration cannot be determined |
| `skip_restore_if_idle` | If `true`, do not restore prior playback when a speaker was idle before the bugle call |
| `latitude` / `longitude` | Your coordinates, used to calculate local sunset time |
| `timezone` | IANA timezone name (e.g. `"America/New_York"`) |
| `sunset_offset_minutes` | Optional offset in minutes from sunset (negative = before, positive = after). Defaults to `0` |

### `speakers` array

Each entry in `speakers` can be either a plain IP address string (legacy) or an object:

| Field | Description |
|-------|-------------|
| `ip` | **Required.** IP address of the Sonos speaker. |
| `name` | Optional friendly name (auto-populated from Sonos discovery during setup). |
| `volume` | Optional per-speaker playback volume (0–100). Overrides the top-level `volume` for this speaker only. |

**Per-speaker volume example:**

```json
"volume": 30,
"speakers": [
  { "ip": "10.0.40.32", "name": "Flag",          "volume": 50 },
  { "ip": "10.0.40.41", "name": "Backyard Left",  "volume": 80 },
  { "ip": "10.0.40.42", "name": "Backyard Right", "volume": 80 },
  { "ip": "10.0.40.55", "name": "Office" }
]
```

Volume resolution order for each speaker: **`speaker.volume`** → **top-level `volume`** → default **30**.

The `Office` speaker above has no explicit `volume`, so it plays at the top-level `volume` (30 in this example).

> **Legacy format still supported:** `"speakers": ["10.0.40.32", "10.0.40.41"]` (plain IP strings) continues to work and is automatically migrated to the object format the next time `setup.sh` is run.

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

## 🔄 Boot recovery

When the LXC container starts (after a reboot, host outage, or migration), `flag-boot-reschedule.service` runs once to recompute today's sunset and rewrite the sunset timer's `OnCalendar` value. Combined with `Persistent=false` on all timers, this means: after any outage, schedules resume cleanly at their next correct fire time, and no missed play is ever replayed late.

> **Missed plays are intentionally skipped, never replayed later.** If the container or host is down at the scheduled time, that play is simply missed — there is no catch-up audio at an unexpected hour.

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

If it works, you'll hear the audio play on your configured Sonos speaker(s) and see log output in `/opt/flag/sonos_play.log`.

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
  By design, the nightly 02:00 reschedule run rewrites the sunset timer's unit file and leaves the timer active across the rewrite. systemd re-reads the updated `OnCalendar` line on `daemon-reload` and re-arms the already-active timer — no explicit `start` or `restart` is needed or safe to use. Starting (or restarting) a sunset timer causes systemd to invoke the associated service immediately — an unwanted audio play. If you ever need to immediately activate the sunset timer manually, run:  
  `sudo systemctl start flag-taps.timer`
- **A speaker is not found or unreachable?**  
  `sonos_play.py` logs a warning and skips the unreachable speaker — the remaining reachable speakers continue with synchronized playback. If **all** configured speakers are unreachable, the script exits with a non-zero code (so systemd marks the unit as failed). Check `/opt/flag/sonos_play.log` for `WARNING: Speaker at <IP> is unreachable` messages.
- **How does grouping work with multiple speakers?**  
  When playback starts, each target speaker is unjoined from its current group and temporarily placed under a single "bugle coordinator" (the first IP in the `speakers` list). Sonos keeps all members in sync automatically. After playback, each speaker rejoins its original group and transport state is restored.
- **Does each speaker play at the same volume?**  
  Yes. Every speaker in the `speakers` list is set to the configured `volume` for the duration of the bugle call. Each speaker's original volume is restored afterward.
- **Will pre-existing speaker groups be disrupted?**  
  Only temporarily. Each speaker is unjoined before playback and rejoined to its original group afterward. Speakers that are **not** in the `speakers` config list are never touched.

---

## 🙏 Credits

Created by agster27.  
Inspired by tradition, powered by Python and Sonos.

---
