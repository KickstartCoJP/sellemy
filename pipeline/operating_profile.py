from pathlib import Path
import json
from recovery_provider_policy import active_policy
ROOT=Path(__file__).resolve().parents[1]

def normal_profile():
    try:
        config=json.loads((ROOT/'config/operating-profile.json').read_text())
    except (OSError,ValueError):
        return False
    return config.get('schema')=='sellemy-operating-profile/v1' and config.get('mode')=='normal' and active_policy() is None
