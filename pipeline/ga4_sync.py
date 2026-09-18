from __future__ import annotations
import argparse, json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Filter, FilterExpression, Metric, RunReportRequest
from google.oauth2 import service_account

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config' / 'ga4.json'
DEFAULT_STATE_DIR = Path.home() / 'Library' / 'Application Support' / 'Sellemy' / 'analytics'

def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))

def client_from_config(cfg):
    creds=service_account.Credentials.from_service_account_file(str(Path(cfg['credential_file']).expanduser()),scopes=['https://www.googleapis.com/auth/analytics.readonly'])
    return BetaAnalyticsDataClient(credentials=creds)

def run_daily_pages(client,pid,start,end):
    r=client.run_report(RunReportRequest(property=f'properties/{pid}',dimensions=[Dimension(name='date'),Dimension(name='pagePath'),Dimension(name='pageTitle')],metrics=[Metric(name='screenPageViews'),Metric(name='sessions'),Metric(name='activeUsers')],date_ranges=[DateRange(start_date=start,end_date=end)],limit=250000))
    return [{'date':x.dimension_values[0].value,'page_path':x.dimension_values[1].value,'page_title':x.dimension_values[2].value,'page_views':int(x.metric_values[0].value or 0),'sessions':int(x.metric_values[1].value or 0),'active_users':int(x.metric_values[2].value or 0)} for x in r.rows]

def run_daily_clicks(client,pid,start,end,event_name):
    r=client.run_report(RunReportRequest(property=f'properties/{pid}',dimensions=[Dimension(name='date'),Dimension(name='pagePath')],metrics=[Metric(name='eventCount')],date_ranges=[DateRange(start_date=start,end_date=end)],dimension_filter=FilterExpression(filter=Filter(field_name='eventName',string_filter=Filter.StringFilter(value=event_name,match_type=Filter.StringFilter.MatchType.EXACT))),limit=250000))
    return {(x.dimension_values[0].value,x.dimension_values[1].value):int(x.metric_values[0].value or 0) for x in r.rows}

def run_daily_click_detail(client,pid,start,end,event_name):
    dims=['date','pagePath','linkUrl','linkText','linkClasses','linkId']
    r=client.run_report(RunReportRequest(property=f'properties/{pid}',dimensions=[Dimension(name=x) for x in dims],metrics=[Metric(name='eventCount')],date_ranges=[DateRange(start_date=start,end_date=end)],dimension_filter=FilterExpression(filter=Filter(field_name='eventName',string_filter=Filter.StringFilter(value=event_name,match_type=Filter.StringFilter.MatchType.EXACT))),limit=250000))
    return [{'date':x.dimension_values[0].value,'page_path':x.dimension_values[1].value,'link_url':x.dimension_values[2].value or None,'product_name':x.dimension_values[3].value or None,'platform':x.dimension_values[4].value or None,'product_id':x.dimension_values[5].value or None,'click_count':int(x.metric_values[0].value or 0)} for x in r.rows]

def merge_raw(path,rows,start,end):
    old=[]
    if path.exists(): old=json.loads(path.read_text(encoding='utf-8')).get('rows',[])
    start_key=start.replace('-',''); end_key=end.replace('-','')
    merged={(r['date'],r['page_path']):r for r in old if not (start_key <= r['date'] <= end_key)}
    for r in rows: merged[(r['date'],r['page_path'])]=r
    return sorted(merged.values(),key=lambda r:(r['date'],r['page_path']))

def merge_detail(path,rows,start,end):
    old=[]
    if path.exists(): old=json.loads(path.read_text(encoding='utf-8')).get('rows',[])
    start_key=start.replace('-',''); end_key=end.replace('-','')
    kept=[r for r in old if not (start_key <= r['date'] <= end_key)]
    return sorted(kept+rows,key=lambda r:(r['date'],r['page_path'],r.get('platform') or '',r.get('product_id') or '',r.get('link_url') or ''))

def article_identity(path):
    for c in ('beauty','dailygoods','gadget'):
        prefix=f'/article/{c}/'
        if path.startswith(prefix) and path.endswith('.html'):
            return c, path[len(prefix):-5]
    return None, None

def aggregate(rows,days=28):
    cutoff=(date.today()-timedelta(days=days-1)).strftime('%Y%m%d')
    agg=defaultdict(lambda:{'page_views':0,'sessions':0,'active_users':0,'affiliate_clicks':0,'ctr_page_views':0,'click_available':False})
    for r in rows:
        if r['date']<cutoff: continue
        x=agg[r['page_path']]
        for k in ('page_views','sessions','active_users'): x[k]+=int(r.get(k,0) or 0)
        if r.get('affiliate_clicks') is not None:
            x['affiliate_clicks']+=int(r['affiliate_clicks']); x['ctr_page_views']+=int(r.get('page_views',0) or 0); x['click_available']=True
        x['page_title']=r.get('page_title','')
    pages=[]
    for path,x in agg.items():
        available=x.pop('click_available'); clicks=x['affiliate_clicks'] if available else None; ctr_pv=x['ctr_page_views'] if available else None
        x['affiliate_clicks']=clicks; x['ctr_page_views']=ctr_pv; x['affiliate_ctr']=round(clicks/ctr_pv,6) if clicks is not None and ctr_pv else (0.0 if clicks is not None else None)
        pages.append({'page_path':path,**x})
    pages.sort(key=lambda x:(-x['page_views'],x['page_path']))
    return pages

def feedback_from(rows,days=28):
    cutoff=(date.today()-timedelta(days=days-1)).strftime('%Y%m%d')
    grouped=defaultdict(lambda:{'page_views':0,'affiliate_clicks':0,'ctr_page_views':0,'click_available':False})
    for r in rows:
        if r['date']<cutoff: continue
        cat,slug=article_identity(r['page_path'])
        if not cat: continue
        key=(cat,slug,r['page_path'])
        grouped[key]['page_views']+=int(r.get('page_views',0) or 0)
        if r.get('affiliate_clicks') is not None:
            grouped[key]['affiliate_clicks']+=int(r['affiliate_clicks']); grouped[key]['ctr_page_views']+=int(r.get('page_views',0) or 0); grouped[key]['click_available']=True
    topic=[]; cats=defaultdict(lambda:{'page_views':0,'affiliate_clicks':0,'ctr_page_views':0,'click_available':False})
    for (cat,slug,path),v in grouped.items():
        pv=v['page_views']; clicks=v['affiliate_clicks'] if v['click_available'] else None; ctr_pv=v['ctr_page_views'] if v['click_available'] else None
        topic.append({'category':cat,'intent_key':slug,'page_path':path,'page_views':pv,'affiliate_clicks':clicks,'ctr_page_views':ctr_pv,'affiliate_ctr':round(clicks/ctr_pv,6) if clicks is not None and ctr_pv else (0.0 if clicks is not None else None)})
        cats[cat]['page_views']+=pv
        if clicks is not None: cats[cat]['affiliate_clicks']+=clicks; cats[cat]['ctr_page_views']+=ctr_pv; cats[cat]['click_available']=True
    for cat,v in cats.items():
        pv=v['page_views']; clicks=v['affiliate_clicks'] if v['click_available'] else None; ctr_pv=v['ctr_page_views'] if v['click_available'] else None
        topic.append({'category':cat,'intent_key':'*','page_views':pv,'affiliate_clicks':clicks,'ctr_page_views':ctr_pv,'affiliate_ctr':round(clicks/ctr_pv,6) if clicks is not None and ctr_pv else (0.0 if clicks is not None else None)})
    return {'source':'GA4','generated_at':datetime.now(timezone.utc).isoformat(),'range_days':days,'topic_metrics':topic,'product_metrics':[]}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--backfill',action='store_true'); ap.add_argument('--start-date'); ap.add_argument('--days',type=int,default=28); args=ap.parse_args()
    cfg=load_config(); state=DEFAULT_STATE_DIR; state.mkdir(parents=True,exist_ok=True)
    raw_path=state/'ga4_daily_raw.json'; detail_path=state/'ga4_affiliate_click_detail.json'; latest_path=state/'ga4_latest.json'; feedback_path=state/'analytics_feedback.json'
    today=date.today(); end=today.isoformat()
    if args.backfill: start=args.start_date or cfg.get('raw_start_date','2025-01-01')
    else: start=(today-timedelta(days=int(cfg.get('refresh_days',3))-1)).isoformat()
    client=client_from_config(cfg)
    event_name=cfg.get('affiliate_click_event_name','affiliate_click'); formal_start=cfg['affiliate_click_formal_start_date'].replace('-','')
    pages=run_daily_pages(client,cfg['property_id'],start,end); clicks=run_daily_clicks(client,cfg['property_id'],start,end,event_name); detail=run_daily_click_detail(client,cfg['property_id'],start,end,event_name)
    page_map={(r['date'],r['page_path']):r for r in pages}
    for key,count in clicks.items():
        row=page_map.setdefault(key,{'date':key[0],'page_path':key[1],'page_title':'','page_views':0,'sessions':0,'active_users':0})
        row['affiliate_clicks']=count
    for r in page_map.values():
        r['affiliate_clicks']=None if r['date']<formal_start else int(r.get('affiliate_clicks',0) or 0)
    rows=merge_raw(raw_path,list(page_map.values()),start,end); detail_rows=merge_detail(detail_path,detail,start,end)
    raw={'source':'GA4','grain':'date_x_pagePath','generated_at':datetime.now(timezone.utc).isoformat(),'property_id':cfg['property_id'],'measurement_id':cfg.get('measurement_id'),'stock_start_date':cfg.get('raw_start_date','2025-01-01'),'affiliate_click_event_name':event_name,'affiliate_click_definition_version':cfg.get('affiliate_click_definition_version'),'affiliate_click_cutover_at':cfg.get('affiliate_click_cutover_at'),'affiliate_click_formal_start_date':cfg.get('affiliate_click_formal_start_date'),'rows':rows}
    raw_path.write_text(json.dumps(raw,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    detail_doc={'source':'GA4','grain':'date_x_pagePath_x_platform_x_product_x_link','generated_at':raw['generated_at'],'event_name':event_name,'formal_start_date':cfg.get('affiliate_click_formal_start_date'),'rows':detail_rows}
    detail_path.write_text(json.dumps(detail_doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    latest={'source':'GA4','generated_at':raw['generated_at'],'range_days':args.days,'affiliate_click_formal_start_date':cfg.get('affiliate_click_formal_start_date'),'pages':aggregate(rows,args.days)}
    latest_path.write_text(json.dumps(latest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    feedback_path.write_text(json.dumps(feedback_from(rows,args.days),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    dates=sorted({r['date'] for r in rows})
    print('GA4_SYNC_OK'); print('FETCH',start,end); print('RAW_ROWS',len(rows)); print('RAW_DATE_RANGE',dates[0] if dates else '-',dates[-1] if dates else '-'); print('LATEST_PAGES',len(latest['pages'])); print('AFFILIATE_DETAIL_ROWS',len(detail_rows)); print('AFFILIATE_FORMAL_START',cfg.get('affiliate_click_formal_start_date'))
if __name__=='__main__': main()
