import json
import time
from pathlib import Path

state = json.loads(Path('/data/health.json').read_text())
assert state['ready'] and time.time() - state['updated'] < 90
