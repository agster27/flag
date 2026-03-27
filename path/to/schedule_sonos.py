# Update in schedule_sonos.py for sunset timers

def create_timer(schedule):
    if schedule.time == 'sunset':
        return Timer(schedule.time, persistent=False)
    return Timer(schedule.time, persistent=True)

# Updated handler in _build_service_unit

def _build_service_unit():
    exec_start = "flock /run/flag.lock python -m service"
    return exec_start