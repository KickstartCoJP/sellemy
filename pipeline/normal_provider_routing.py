"""Fresh-read stage model routing, with fail-closed bounds and no secret material."""
from __future__ import annotations
import json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class NormalProviderConfigError(ValueError):pass

def get_route(stage:str)->dict:
    if stage not in {'planner','writer'}:raise NormalProviderConfigError('unknown stage')
    try:cfg=json.loads((ROOT/'config/normal-provider-routing.json').read_text())
    except (OSError,ValueError) as ex:raise NormalProviderConfigError('normal routing config missing or invalid') from ex
    if cfg.get('schema')!='sellemy-growth-provider-routing/v1':raise NormalProviderConfigError('wrong routing schema')
    value=(cfg.get('stages') or {}).get(stage)
    if not isinstance(value,dict):raise NormalProviderConfigError('stage route missing')
    a=value.get('api_weight');c=value.get('codex_weight')
    if not isinstance(a,(float,int)) or isinstance(a,bool) or not isinstance(c,(float,int)) or isinstance(c,bool):raise NormalProviderConfigError('invalid weights')
    if not all(math.isfinite(v) and 0<=v<=100 for v in (a,c)) or (a+c)!=100:raise NormalProviderConfigError('weights must sum to 100')
    if not all(isinstance(value.get(k),str) and value[k].strip() for k in ('api_model','escalation_model')):raise NormalProviderConfigError('missing model')
    return dict(value)
