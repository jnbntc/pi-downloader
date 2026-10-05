import json
import time

import config


def healthy():
    try:
        state = json.loads((config.STATE_DIR / "health.json").read_text())
        return 0 <= time.time() - state["timestamp"] < 35 and state["workers"] == config.MAX_CONCURRENT_TASKS
    except (OSError, ValueError, KeyError, TypeError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if healthy() else 1)
