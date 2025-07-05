# 🎖️ Sonos Scheduled Playback: Colors & Taps Automation

This project automates the daily playback of **"Colors"** at 8:00 AM and **"Taps"** at sunset using a Sonos speaker system and a virtual server or container.

Powered by:
- 🐍 Python
- 📡 SoCo (Sonos Control)
- 🕓 Cron scheduling
- 🌇 Astral for sunset calculation
- 📁 Dynamic MP3 duration detection via Mutagen
- 🧠 Intelligent playback restore

---

## ✅ Features

- Play `colors.mp3` daily at **8:00 AM**
- Play `taps.mp3` daily at **sunset** (automatically calculated)
- Pauses current playback and **resumes previous music** after the scheduled track
- **Skips restore** if nothing was playing before
- Logs all playback events to `sonos_play.log`
- Hosted via built-in Python HTTP server (on `flag.aghy.home:8000`)

---

## 🖥️ System Requirements

- Python 3.8+
- Sonos speaker (on the same network)
- Debian/Ubuntu VM or LXC running on Proxmox
- Python virtual environment (recommended)
- MP3 files hosted locally (via HTTP)

---

## 📁 Project Structure

