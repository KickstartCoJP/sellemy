from __future__ import annotations
import json
from growth_recovery import summarize_execution

if __name__ == '__main__':
    print(json.dumps(summarize_execution(), ensure_ascii=False, indent=2))
