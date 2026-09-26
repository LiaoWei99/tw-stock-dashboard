from flask import Flask, jsonify, send_from_directory, request
from urllib.request import Request, urlopen
from urllib.parse import quote, urlencode
import json, os, time, xml.etree.ElementTree as ET, re
import threading, math, datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from collections import OrderedDict
app=Flask(__name__, static_folder='.')
BASE=os.path.dirname(__file__)

def fetch(url, accept='application/json'):
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 TW-Stock-Dashboard/3.0','Accept':accept})
    with urlopen(req,timeout=5) as r: return r.read()
def get_json(url): return json.loads(fetch(url).decode('utf-8'))
def num_or_none(v):
    try:
        if v is None or isinstance(v, bool): return None
        x = float(str(v).replace(',', '').replace('%', '').strip())
        return x if math.isfinite(x) else None
    except (ValueError, TypeError): return None

def num_or_zero(v):
    value = num_or_none(v)
    return 0.0 if value is None else value

_CACHE_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix='cache')
_CORE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='core')
_HISTORY_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='history')
_NEWS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='news')

class TTLCache:
    """Bounded process-local cache with a single in-flight loader per key.

    Failed loads use a short backoff; expired values are never labelled fresh.
    All returned values are copies, preventing callers from mutating shared data.
    """
    def __init__(self, ttl, maxsize=512, executor=None):
        self.ttl, self.maxsize = ttl, maxsize
        self.executor = executor or _CACHE_POOL
        self.lock = threading.RLock()
        self.entries, self.pending = OrderedDict(), {}

    def _load(self, key, loader):
        try:
            value = loader()
            error = None
        except Exception as exc:
            value, error = None, str(exc)[:240]
            app.logger.warning('source unavailable key=%s error=%s', key, error)
        with self.lock:
            ttl = self.ttl if error is None and value is not None else min(self.ttl, 15)
            if isinstance(value, dict) and value.get('errors'): ttl = min(ttl, 60)
            self.entries[key] = (time.monotonic() + ttl, value, error)
            self.entries.move_to_end(key)
            while len(self.entries) > self.maxsize: self.entries.popitem(last=False)
            self.pending.pop(key, None)
        return value

    def get(self, key, loader, wait=True, timeout=6):
        with self.lock:
            entry = self.entries.get(key)
            if entry and entry[0] > time.monotonic(): return deepcopy(entry[1])
            future = self.pending.get(key)
            if future is None:
                if len(self.pending) >= 64: return None
                future = self.executor.submit(self._load, key, loader)
                self.pending[key] = future
        if not wait: return None
        try: return deepcopy(future.result(timeout=timeout))
        except TimeoutError: return None

    def status(self, key):
        with self.lock:
            if key in self.pending: return 'Loading'
            e = self.entries.get(key)
            return 'OK' if e and e[0] > time.monotonic() and e[1] is not None and not e[2] else 'Unavailable'

    def error(self, key):
        with self.lock:
            e = self.entries.get(key)
            return e[2] if e else None

_QUOTE_CACHE = TTLCache(5, executor=_CORE_POOL)
_VALUATION_CACHE = TTLCache(900, 4, executor=_CORE_POOL)
_INSTITUTIONAL_CACHE = TTLCache(900, 4, executor=_CORE_POOL)
_NEWS_CACHE = TTLCache(600)
_HISTORY_CACHE = TTLCache(3600, 128)

def market_key(market):
    if market not in ('上市', '上櫃'): raise ValueError('unknown market')
    return market

_COMPANY_CACHE={'rows':[],'ts':0}
_COMPANY_INDEX={}
_COMPANY_LOCK=threading.Lock()
_COMPANY_TTL=21600

def _load_company_rows():
    # Remote refresh: background only. Request handlers never call this.
    rows=[]
    sources=[('上市','https://openapi.twse.com.tw/v1/opendata/t187ap03_L'),('上櫃','https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O')]
    for market,url in sources:
        try:
            data=get_json(url)
            for r in data:
                code=str(r.get('公司代號') or r.get('SecuritiesCompanyCode') or '').strip()
                name=str(r.get('公司簡稱') or r.get('公司名稱') or r.get('CompanyAbbreviation') or r.get('CompanyName') or '').strip()
                if not code or not name: continue
                rows.append({'code':code,'name':name,'market':market,'industry':str(r.get('產業別') or r.get('產業類別') or r.get('SecuritiesIndustryCode') or r.get('Industry') or '').strip(),'business':str(r.get('主要經營業務') or r.get('營業項目') or r.get('BusinessScope') or '').strip(),'raw':r})
        except Exception: continue
    return rows

def _install_company_rows(rows):
    if not rows:return False
    with _COMPANY_LOCK:
        merged={x['code']:x for x in _COMPANY_CACHE['rows']}
        merged.update({x['code']:x for x in rows})
        rows=list(merged.values())
        _COMPANY_CACHE['rows']=rows; _COMPANY_CACHE['ts']=time.time()
        _COMPANY_INDEX.clear(); _COMPANY_INDEX.update({x['code']:x for x in rows if x.get('code')})
    return True

def company_rows():
    # Memory only: no network I/O on the request path.
    with _COMPANY_LOCK: return deepcopy(_COMPANY_CACHE.get('rows') or [])

def cached_company(code):
    with _COMPANY_LOCK: return deepcopy(_COMPANY_INDEX.get(str(code)))

def _parse_mis_row(r,market):
    num = num_or_none
    code=str(r.get('c') or '').strip(); last=num(r.get('z')); prev=num(r.get('y'))
    change=(last-prev) if last is not None and prev is not None else None
    return {'code':code,'name':r.get('n') or r.get('nf') or '','market':market,'price':last,'prev_close':prev,'change':round(change,2) if change is not None else None,'pct':round(change/prev*100,2) if change is not None and prev else None,'open':num(r.get('o')),'high':num(r.get('h')),'low':num(r.get('l')),'volume':num(r.get('v')),'time':r.get('t') or '','date':r.get('d') or '','source':'TWSE MIS','price_status':'last_trade' if last is not None else 'Unavailable','note':None if last is not None else 'MIS 未提供最後成交價；不以昨收或推估值代替。'}

def _discover_quote(code):
    chans=f'tse_{code}.tw|otc_{code}.tw'
    url='https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch='+quote(chans,safe='|_.')+'&json=1&delay=0'
    data=get_json(url)
    for r in data.get('msgArray') or []:
        if str(r.get('c') or '').strip()!=str(code):continue
        ex=str(r.get('ex') or r.get('ch') or '').lower(); market='上櫃' if 'otc' in ex else ('上市' if 'tse' in ex else None)
        if market is None: continue
        q=_parse_mis_row(r,market)
        if q.get('name') or q.get('prev_close') is not None or q.get('price') is not None:return q
    return None

def discover_quote(code):
    return _QUOTE_CACHE.get(str(code), lambda: _discover_quote(code))

def company_by_code_fast(code):
    c=cached_company(code)
    if c:return c
    if code.isdigit() and 4<=len(code)<=6:
        try:q=discover_quote(code)
        except Exception:q=None
        if q and q.get('name'):
            c={'code':code,'name':q['name'],'market':q['market'],'industry':'','business':'','raw':{}}
            with _COMPANY_LOCK: _COMPANY_INDEX[code]=c
            return c
    return None

def _mis_quote(code,market=None):
    if not market:return _discover_quote(code)
    ex='tse' if market_key(market)=='上市' else 'otc'
    url='https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch='+quote(f'{ex}_{code}.tw',safe='|_.')+'&json=1&delay=0'
    data=get_json(url); rows=data.get('msgArray') or []
    return next((_parse_mis_row(r,market) for r in rows if str(r.get('c')) == str(code)), None)

def mis_quote(code, market=None):
    return _QUOTE_CACHE.get(str(code), lambda: _mis_quote(code,market))

def sentiment(title):
    t=(title or '').lower()
    bull=['創高','新高','上修','成長','增加','大增','擴產','接單','訂單','獲利','轉盈','優於預期','調升','合作','得標','需求強','漲價','營收增','看旺','利多']
    bear=['下修','衰退','減少','大減','虧損','轉虧','砍單','取消訂單','低於預期','調降','停工','裁員','處分','違約','調查','罰款','需求弱','跌價','營收減','利空']
    bs=sum(1 for k in bull if k in t); ss=sum(1 for k in bear if k in t)
    if bs>ss:return '偏多','標題含正向營運／需求／獲利訊號；仍需閱讀原文確認。'
    if ss>bs:return '偏空','標題含負向營運／需求／風險訊號；仍需閱讀原文確認。'
    return '中性／待確認','僅憑標題無法可靠判定方向，需閱讀原文。'

@app.get('/')
@app.get('/live.html')
def home(): return send_from_directory(BASE,'live.html')
@app.get('/manifest.webmanifest')
def manifest(): return send_from_directory(BASE,'manifest.webmanifest',mimetype='application/manifest+json')
@app.get('/sw.js')
def sw(): return send_from_directory(BASE,'sw.js',mimetype='application/javascript')
@app.get('/health')
def health(): return jsonify({'ok':True,'service':'tw-stock-dashboard','version':'9.2.3'})

@app.get('/api/twse/realtime')
def realtime():
    ex=request.args.get('ex','tse'); codes=request.args.get('codes','2330').split(',')[:30]
    chans='|'.join(f'{ex}_{c}.tw' for c in codes if c.isdigit())
    url='https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch='+quote(chans,safe='|_.')+'&json=1&delay=0'
    try:return jsonify({'ok':True,'source':'TWSE MIS','data':get_json(url),'ts':int(time.time()*1000)})
    except Exception as e:return jsonify({'ok':False,'error':str(e),'source':'TWSE MIS'}),502

@app.get('/api/market/top')
def market_top():
    try:
        limit=max(5,min(int(request.args.get('limit','20')),5000)); rows=get_json('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'); out=[]
        for r in rows:
            code=str(r.get('Code',''))
            if not code.isdigit():continue
            value=num_or_none(r.get('TradeValue'));vol=num_or_none(r.get('TradeVolume'));close=num_or_none(r.get('ClosingPrice'));change=num_or_none(r.get('Change'));prev=close-change if close is not None and change is not None else None
            out.append({'code':code,'name':r.get('Name',''),'close':close,'change':change,'pct':round(change/prev*100,2) if prev and change is not None else None,'value':value,'volume':vol,'open':num_or_none(r.get('OpeningPrice')),'high':num_or_none(r.get('HighestPrice')),'low':num_or_none(r.get('LowestPrice')),'vwap':round(value/vol,2) if vol else None})
        out.sort(key=lambda x:num_or_zero(x['value']),reverse=True)
        return jsonify({'ok':True,'source':'TWSE OpenAPI STOCK_DAY_ALL','timing':'official dataset; not tick-real-time','rows':out[:limit],'ts':int(time.time()*1000)})
    except Exception as e:return jsonify({'ok':False,'error':str(e)}),502

@app.get('/api/company/search')
def company_search():
    q=request.args.get('q','').strip()
    if not q:return jsonify({'ok':True,'rows':[]})
    if q.isdigit():
        c=company_by_code_fast(q)
        return jsonify({'ok':True,'rows':[{k:v for k,v in c.items() if k!='raw'}] if c else []})
    rows=company_rows()
    ql=q.lower()
    out=[{k:v for k,v in x.items() if k!='raw'} for x in rows if ql in x['name'].lower() or ql in x['code'].lower()]
    return jsonify({'ok':True,'rows':out[:20],'cached':bool(rows),'status':'OK' if rows else 'Loading'})

@app.get('/api/stock/context')
def stock_context():
    code=request.args.get('code','').strip()
    if not code:return jsonify({'ok':False,'error':'code required'}),400
    try:
        c=company_by_code_fast(code)
        if not c:return jsonify({'ok':False,'error':'stock code not found'}),404
        q=None
        try:q=mis_quote(code,c['market'])
        except Exception:pass
        return jsonify({'ok':True,'company':{k:v for k,v in c.items() if k!='raw'},'quote':q,'quote_status':'ok' if q else 'Unavailable','ts':int(time.time()*1000)})
    except Exception as e:return jsonify({'ok':False,'error':str(e)}),502


def news_rss(query, limit=12):
    url='https://news.google.com/rss/search?'+urlencode({'q':query,'hl':'zh-TW','gl':'TW','ceid':'TW:zh-Hant'})
    root=ET.fromstring(fetch(url,'application/rss+xml, application/xml, text/xml'))
    out=[]
    for it in root.findall('.//item')[:limit]:
        title=(it.findtext('title') or '').strip(); link=(it.findtext('link') or '').strip(); pub=(it.findtext('pubDate') or '').strip(); source=''
        s=it.find('source'); source=(s.text or '').strip() if s is not None else ''
        out.append({'title':title,'url':link,'published':pub,'source':source})
    return out

@app.get('/api/news/global')
def news_global():
    query=request.args.get('q') or '(台股 OR 台灣股市 OR 半導體 OR AI OR NVIDIA OR 台積電 OR 聯準會 OR Fed OR 美債 OR 美元 OR 原油 OR 中國經濟 OR 地緣政治) when:1d'
    try:return jsonify({'ok':True,'source':'Google News RSS 聚合','rows':news_rss(query,20),'ts':int(time.time()*1000),'note':'新聞為聚合來源，更新速度取決於原始媒體與聚合索引。'})
    except Exception as e:return jsonify({'ok':False,'error':str(e)}),502

def _field(r, needles):
    for k,v in r.items():
        ks=str(k)
        if any(x in ks for x in needles): return str(v or '').strip()
    return ''

def official_material_info(code, market, limit=12):
    url=('https://openapi.twse.com.tw/v1/opendata/t187ap04_L' if market=='上市'
         else 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O')
    out=[]
    try:
        for r in get_json(url):
            rc=_field(r,['公司代號','證券代號','SecuritiesCompanyCode','CompanyCode'])
            if rc != code: continue
            title=_field(r,['主旨','重大訊息','說明','Subject'])
            date=_field(r,['發言日期','公告日期','Date'])
            tm=_field(r,['發言時間','公告時間','Time'])
            if title:
                se,reason=sentiment(title)
                out.append({'title':title,'url':'https://mops.twse.com.tw/mops/web/index','published':(date+' '+tm).strip(),
                            'source':'公開資訊觀測站／官方重大訊息','category':'官方重大訊息',
                            'relation':'公司直接','sentiment':se,'sentiment_reason':reason})
            if len(out)>=limit: break
    except Exception:
        raise
    return out

def layered_stock_news(code, name, industry, market):
    jobs=[]
    if name:
        jobs.append(('公司直接新聞', lambda: news_rss(f'"{name}" {code} when:14d', 15)))
    if industry and not industry.isdigit():
        jobs.append(('產業／供應鏈', lambda: news_rss(f'{industry} 台股 when:7d', 10)))
    if code and market in ('上市','上櫃'):
        jobs.append(('官方重大訊息', lambda: official_material_info(code,market,12)))
    futures=[(cat,_NEWS_POOL.submit(fn)) for cat,fn in jobs]
    rows=[]; errors=[]; seen=set()
    for cat,f in futures:
        try:
            items=f.result(timeout=8)
            for item in items:
                x=dict(item); title=x.get('title',''); key=re.sub(r'\s+',' ',title).strip()
                if not key or key in seen: continue
                seen.add(key); x['category']=cat
                x['relation']='公司直接' if name and name in title else ('產業相關' if cat=='產業／供應鏈' else '可能相關')
                x['sentiment'],x['sentiment_reason']=sentiment(title)
                rows.append(x)
        except Exception as exc: errors.append({'source':cat,'error':str(exc)[:180]})
    rows.sort(key=lambda x: x['category']!='官方重大訊息')
    return {'rows':rows[:30], 'errors':errors}

@app.get('/api/news/stock')
def news_stock():
    code=request.args.get('code','').strip(); name=request.args.get('name','').strip()
    industry=request.args.get('industry','').strip(); market=request.args.get('market','').strip()
    if not code and not name:return jsonify({'ok':False,'error':'code/name required'}),400
    try:
        c=cached_company(code) if code else None
        if c:
            name=c['name']; market=c['market']
            industry=stock_group(c)
        elif industry.isdigit():
            industry=''
        if industry=='Unavailable': industry=''
        key=(code,name,industry,market)
        result=_NEWS_CACHE.get(key,lambda:layered_stock_news(code,name,industry,market),wait=False)
        return jsonify({'ok':True,'source':'官方重大訊息 + Google News 分層聚合',
            'rows':(result or {}).get('rows',[]), 'errors':(result or {}).get('errors',[]),
            'status':('OK' if result.get('rows') else 'Unavailable') if result is not None else _NEWS_CACHE.status(key),
            'sentiment_note':'標題規則分類，需閱讀原文確認，不代表股價預測。'})
    except Exception as e:return jsonify({'ok':False,'error':str(e)}),502

@app.get('/api/futures/ranking')
def futures_ranking():
    base=os.getenv('FUTURES_PROVIDER_URL'); token=os.getenv('FUTURES_PROVIDER_TOKEN')
    if base and token:
        try:
            req=Request(base,headers={'Authorization':'Bearer '+token,'User-Agent':'TW-Stock-Dashboard/3.0'})
            with urlopen(req,timeout=10) as r:return jsonify({'ok':True,'mode':'licensed-realtime','source':os.getenv('FUTURES_PROVIDER_NAME','licensed-feed'),'data':json.loads(r.read().decode())})
        except Exception as e:return jsonify({'ok':False,'error':str(e)}),502
    return jsonify({'ok':True,'mode':'unavailable-realtime','rows':[],'source':'TAIFEX','note':'即時期貨開盤漲跌排行需要授權行情源。TAIFEX官方日行情/法人資料依公告時程更新，不能冒充盤中逐筆即時。','configure':['FUTURES_PROVIDER_URL','FUTURES_PROVIDER_TOKEN','FUTURES_PROVIDER_NAME']})

@app.get('/api/market/factors')
def factors():
    return jsonify({'ok':True,'items':[
      {'factor':'美股/費半/NVIDIA','impact':'AI、半導體、伺服器風險偏好','status':'news-live; quote feed optional'},
      {'factor':'台指期夜盤/日盤','impact':'開盤方向、基差、風險情緒','status':'licensed futures feed required for realtime'},
      {'factor':'Fed/美債殖利率/美元','impact':'估值、外資、金融與成長股','status':'news-live; market quote feed optional'},
      {'factor':'原油/黃金/銅','impact':'航運、塑化、原物料、通膨預期','status':'news-live; quote feed optional'},
      {'factor':'中國/日韓市場與匯率','impact':'電子供應鏈、傳產、資金風險偏好','status':'news-live; quote feed optional'},
      {'factor':'地緣政治/關稅/出口管制','impact':'半導體、AI、航運、軍工與避險','status':'news-live'},
      {'factor':'台股重大訊息','impact':'個股事件風險','status':'TWSE OpenAPI available'}]})

@app.get('/api/twse/openapi/<path:dataset>')
def twse_openapi(dataset):
    try:return jsonify({'ok':True,'source':'TWSE OpenAPI','data':get_json('https://openapi.twse.com.tw/v1/'+dataset)})
    except Exception as e:return jsonify({'ok':False,'error':str(e)}),502

@app.get('/api/taifex/status')
def taifex_status():return jsonify({'ok':True,'source':'TAIFEX','mode':'official-daily','note':'Tick-level realtime requires licensed feed.'})


# --- V6 analyst scoring layer ---
def _pick(r, includes):
    for k,v in r.items():
        if all(s in k for s in includes): return v
    return None

def _valuation_rows(market, wait=True):
    market_key(market)
    url=('https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL' if market=='上市'
         else 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis')
    return _VALUATION_CACHE.get(market, lambda:get_json(url), wait=wait) or []

_num_or_none = num_or_none

def first_field(row, *names):
    for name in names:
        if name in row and row[name] is not None and str(row[name]).strip() != '': return row[name]
    return None

def valuation_row(code, market='上市', wait=True):
    try:
        for r in _valuation_rows(market,wait):
            rc=str(r.get('Code') or r.get('SecuritiesCompanyCode') or r.get('SecuritiesCode') or _field(r,['證券代號','股票代號','公司代號'])).strip()
            if rc!=code:continue
            if market=='上市':
                pe=num_or_none(first_field(r, 'PEratio', 'PERatio', '本益比'))
                pb=num_or_none(first_field(r, 'PBratio', 'PBRatio', '股價淨值比'))
                dy=num_or_none(first_field(r, 'YieldRatio', 'DividendYield', 'DividendYield(%)', '殖利率(%)', '殖利率'))
            else:
                pe=num_or_none(first_field(r, 'PriceEarningRatio', 'PEratio', '本益比'))
                pb=num_or_none(first_field(r, 'PriceBookRatio', 'PBratio', '股價淨值比'))
                dy=num_or_none(first_field(r, 'YieldRatio', 'DividendYield', 'DividendYield(%)', '殖利率(%)', '殖利率'))
            return {'pe':pe,'pb':pb,'yield':dy,'date':str(r.get('Date') or r.get('日期') or ''),
                    'source':'TWSE BWIBBU_ALL' if market=='上市' else 'TPEx tpex_mainboard_peratio_analysis'}
    except Exception:pass
    return None

def _institutional_rows(market):
    market_key(market)
    if market=='上櫃':
        return get_json('https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading')
    # TWSE's website publishes T86; /openapi/v1/fund/T86_ALL is not a JSON dataset.
    j=get_json('https://www.twse.com.tw/rwd/zh/fund/T86?response=json&selectType=ALLBUT0999')
    if j.get('stat') != 'OK': raise ValueError('TWSE T86 unavailable: '+str(j.get('stat')))
    return [dict(zip(j.get('fields',[]), r), Date=j.get('date','')) for r in j.get('data',[])]

def institutional_row(code, market='上市'):
    rows=_INSTITUTIONAL_CACHE.get(market,lambda:_institutional_rows(market)) or []
    for r in rows:
        rc=str(first_field(r,'Code','SecuritiesCompanyCode','證券代號') or '').strip()
        if rc!=code: continue
        # Exact net/difference columns only. Never mistake buy volume for net buying.
        foreign=num_or_none(first_field(r,'外陸資買賣超股數(不含外資自營商)',
            'Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference'))
        trust=num_or_none(first_field(r,'投信買賣超股數','SecuritiesInvestmentTrustCompanies-Difference'))
        dealer=num_or_none(first_field(r,'自營商買賣超股數','Dealers-Difference'))
        total=num_or_none(first_field(r,'三大法人買賣超股數','TotalDifference'))
        return {'foreign':foreign,'trust':trust,'dealer':dealer,'total':total,
                'date':str(r.get('Date') or ''),'source':'TWSE T86' if market=='上市' else 'TPEx tpex_3insti_daily_trading',
                'timing':'官方盤後法人資料；單位：股'}
    return None

def _taipei_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))

def _history_month(code, market, year, month):
    if market=='上市':
        url='https://www.twse.com.tw/exchangeReport/STOCK_DAY?'+urlencode({'response':'json','date':f'{year:04d}{month:02d}01','stockNo':code})
    else:
        url='https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?'+urlencode({'response':'json','date':f'{year:04d}/{month:02d}/01','code':code})
    j=get_json(url)
    if str(j.get('stat','')).lower()!='ok': raise ValueError(str(j.get('stat') or 'invalid history response'))
    if market=='上市': data=j.get('data',[])
    else:
        tables=j.get('tables') or []
        data=tables[0].get('data',[]) if tables else []
        fields=tables[0].get('fields',[]) if tables else []
        if not fields or '成交張數' not in str(fields[1]): raise ValueError('unknown TPEx volume unit')
    out=[]; now=_taipei_now()
    for row in data:
        if len(row)<7: continue
        try:
            y,m,d=map(int,str(row[0]).split('/')); y=y+1911 if y<1911 else y
            day=datetime.date(y,m,d)
        except (ValueError,TypeError): continue
        # Do not treat an intraday row as a confirmed closing price.
        if day>now.date() or (day==now.date() and now.hour<15): continue
        op,hi,lo,cl=[num_or_none(row[i]) for i in (3,4,5,6)]
        if cl is None or cl<=0: continue
        vol=num_or_none(row[1])
        if market=='上櫃' and vol is not None: vol*=1000
        out.append({'date':day.isoformat(),'open':op,'high':hi,'low':lo,'close':cl,'volume':vol})
    return out

def _load_history(code,market):
    market_key(market); today=_taipei_now().date(); jobs=[]
    for i in range(12):
        y,m=divmod(today.year*12+today.month-1-i,12); m+=1
        jobs.append((f'{y:04d}-{m:02d}',_HISTORY_POOL.submit(_history_month,code,market,y,m)))
    all_rows=[]; errors=[]; contiguous=True
    for month,future in jobs:
        try:
            rows=future.result()
            if contiguous: all_rows.extend(rows)
        except Exception as exc:
            errors.append({'month':month,'error':str(exc)[:180]}); contiguous=False
    rows=sorted({r['date']:r for r in all_rows}.values(),key=lambda x:x['date'])
    return {'rows':rows,'errors':errors,'requested_months':12,'source':'TWSE STOCK_DAY' if market=='上市' else 'TPEx tradingStock'}

def history_result(code,market,wait=False):
    return _HISTORY_CACHE.get((market,code,12),lambda:_load_history(code,market),wait=wait,timeout=40)

def market_history(code,market='上市',months=12):
    # months retained for compatibility; both research modules ALWAYS share 12 months.
    return (history_result(code,market,wait=True) or {}).get('rows',[])

def analyst_model(company, quote):
    code,market=company['code'],company['market']
    val=valuation_row(code,market); inst=institutional_row(code,market)
    hist=market_history(code,market,12)
    fp=fa=tp=ta=cp=ca=0; freasons=[]; treasons=[]; creasons=[]
    if company.get('industry'): fp+=4; fa+=4; freasons.append('官方產業分類可辨識')
    if company.get('business'): fp+=4; fa+=4; freasons.append('主要業務資料可辨識')
    pe,pb,dy=[(val or {}).get(k) for k in ('pe','pb','yield')]
    if pe is not None:
        fa+=5; fp+=5 if 0<pe<=25 else (3 if 25<pe<=50 else (1 if pe>50 else 0))
        freasons.append('本益比依原規則計分')
    if pb is not None: fa+=3; fp+=3 if 0<pb<=3 else (2 if 3<pb<=6 else (1 if pb>6 else 0))
    if dy is not None: fa+=4; fp+=1
    q=quote or {}; px=q.get('price'); pct=q.get('pct'); op=q.get('open'); hi=q.get('high'); lo=q.get('low')
    if px is not None:
        ta+=2; tp+=2
        if pct is not None: ta+=7; tp+=(5 if pct>0 else 0)+(2 if pct>=2 else 0)
        if op is not None:
            ta+=4
            if px>=op: tp+=4; treasons.append('價格守在開盤價之上')
        if hi is not None and lo is not None and hi>lo:
            ta+=5; position=(px-lo)/(hi-lo); tp+=5 if position>=.8 else (3 if position>=.55 else 1)
    ma20=ma60=ret20=avgvol20=None
    if len(hist)>=20:
        closes=[x['close'] for x in hist]; vols=[x['volume'] for x in hist[-20:]]
        ma20=sum(closes[-20:])/20; ret20=(closes[-1]/closes[-20]-1)*100
        ta+=10; tp+=(5 if closes[-1]>ma20 else 0)+(3 if ret20>0 else 0)+1
        if all(v is not None for v in vols):
            avgvol20=sum(vols)/20; ta+=2
            if avgvol20>0 and vols[-1]>avgvol20: tp+=2; treasons.append('成交量高於20日均量')
    if len(hist)>=60:
        ma60=sum(x['close'] for x in hist[-60:])/60; ta+=10
        tp+=(4 if hist[-1]['close']>ma60 else 0)+(4 if ma20>ma60 else 0)+1
    for key,weight,label in [('total',16,'三大法人合計'),('foreign',10,'外資'),('trust',10,'投信')]:
        value=(inst or {}).get(key)
        if value is not None:
            ca+=weight; cp+=weight if value>0 else (weight/2 if value==0 else 0)
            if value>0: creasons.append(label+'買超')
    if inst and all(inst.get(k) is not None for k in ('total','foreign','trust','dealer')): ca+=4; cp+=4
    # Fixed denominators: missing evidence NEVER redistributes or inflates points.
    f20=round(fp,2) if fa else None; t40=round(tp,2) if ta else None; c40=round(cp,2) if ca else None
    f35=round(fp/20*35,2) if fa else None; t35=round(tp/40*35,2) if ta else None; c30=round(cp/40*30,2) if ca else None
    total_o=round(fp+tp+cp,2) if fa+ta+ca else None
    total_s=round(fp/20*35+tp/40*35+cp/40*30,2) if fa+ta+ca else None
    comp_o=round(fa+ta+ca); comp_s=round(fa/20*35+ta/40*35+ca/40*30)
    enough_o=comp_o>=60 and ta>0 and ca>0
    overnight_ok=bool(enough_o and total_o>=65 and t40>=24 and c40>=20)
    enough_s=len(hist)>=20 and comp_s>=60 and ta>0
    swing_ok=bool(not overnight_ok and enough_s and total_s>=62 and t35>=20)
    plan=None
    lows=[r['low'] for r in hist[-10:]]; highs=[r['high'] for r in hist[-20:]]
    if swing_ok and px is not None and len(hist)>=20 and all(x is not None for x in lows+highs):
        support=min(lows); resistance=max(highs)
        entry_low=max(support,ma20*.985); entry_high=max(entry_low,ma20*1.015)
        stop=min(entry_low*.965,support*.985); risk=max(entry_high-stop,entry_high*.02)
        target1=max(resistance,entry_high+1.5*risk); target2=max(target1,entry_high+2.2*risk)
        plan={'holding':'10–20個交易日' if ma60 and ma20>ma60 else '5–10個交易日',
              'entry':[round(entry_low,2),round(entry_high,2)],'stop':round(stop,2),
              'target1':round(target1,2),'target2':round(target2,2),'rr1':round((target1-entry_high)/risk,2),
              'support':support,'resistance':resistance,'basis':'支撐近10日低點、壓力近20日高點，以收盤確認；情境價格不是預測。'}
    return {'method':'規則式研究評分；固定權重，缺值不補分，不代表保證報酬。',
        'fundamental':{'overnight_score':f20,'swing_score':f35,'available_points':fa,'reasons':freasons,'valuation':val},
        'technical':{'overnight_score':t40,'swing_score':t35,'available_points':ta,'reasons':treasons,'ma20':ma20,'ma60':ma60,'return20':ret20},
        'chips':{'overnight_score':c40,'swing_score':c30,'available_points':ca,'reasons':creasons,'institutional':inst},
        'overnight':{'score':total_o,'completeness':comp_o,'eligible':overnight_ok,'status':'符合' if overnight_ok else ('不符合' if enough_o else '資料不足'),'threshold':'基本20／技術40／籌碼40；完整度>=60、總分>=65、技術>=24、籌碼>=20'},
        'swing':{'score':total_s,'completeness':comp_s,'eligible':swing_ok,'status':'符合' if swing_ok else ('不符合' if enough_s else '資料不足'),'plan':plan,'threshold':'基本35／技術35／籌碼30；隔日沖不符合、至少20日歷史、完整度>=60、總分>=62、技術>=20'},
        'missing':[label for label,ok in [('完整基本面',fa==20),('完整技術面',ta==40),('完整法人淨買賣超',ca==40)] if not ok]}

@app.get('/api/stock/analysis')
def stock_analysis():
    code=request.args.get('code','').strip()
    if not code:return jsonify({'ok':False,'error':'code required'}),400
    try:
        c=company_by_code_fast(code)
        if not c:return jsonify({'ok':False,'error':'stock code not found'}),404
        q=None; errors=[]
        try:q=mis_quote(code,c['market'])
        except Exception as e: errors.append({'part':'行情','error':str(e)[:180]})
        try:
            a=analyst_model(c,q)
        except Exception as e:
            errors.append({'part':'分析模型','error':str(e)[:180]}); a=None
        return jsonify({'ok':True,'partial':bool(errors),'company':{k:v for k,v in c.items() if k!='raw'},
                        'quote':q,'analysis':a,'errors':errors,'ts':int(time.time()*1000)})
    except Exception as e:return jsonify({'ok':False,'error':str(e)}),502


def safe_call(fn,default=None):
    try:return fn(),None
    except Exception as e:return default,str(e)[:180]
def stock_group(c):
    overrides={'2408':'記憶體','6669':'AI伺服器','8358':'PCB／銅箔'}
    if c.get('code') in overrides: return overrides[c['code']]
    industry=(c.get('industry') or '').strip(); business=(c.get('business') or '').strip(); text=(industry+' '+business).lower()
    rules=[
      (['記憶體','dram','nand'], '記憶體'),(['伺服器','server'], 'AI伺服器'),
      (['光通訊','光纖'], '光通訊'),(['pcb','印刷電路','載板'], 'PCB／載板'),
      (['散熱','thermal'], '散熱'),(['半導體','晶圓','積體電路','ic設計'], '半導體'),
      (['銀行','金控','保險','證券'], '金融'),(['航運','海運','貨櫃'], '航運'),
      (['營建','建設'], '營建'),(['生技','製藥','醫療'], '生技醫療')]
    for keys,label in rules:
        if any(k in text for k in keys):return label
    return {'24':'半導體','25':'電腦及週邊設備','26':'光電','27':'通信網路','28':'電子零組件','17':'金融保險','20':'其他'}.get(industry, industry if industry and not industry.isdigit() else 'Unavailable')

def technical_snapshot(c,q):
    h=market_history(c['code'],c['market'],12)
    if not h:return {'status':'資料不足','history_days':0,'group':stock_group(c)}
    cl=[x['close'] for x in h if x.get('close')]; vo=[x.get('volume') for x in h if x.get('close')]
    def ma(k):return round(sum(cl[-k:])/k,2) if len(cl)>=k else None
    ms={str(k):ma(k) for k in [5,10,20,60,120,240]}; px=cl[-1]; r=h[-20:]
    lows=[x.get('low') for x in h[-10:]]; highs=[x.get('high') for x in h[-20:]]; av=sum(vo[-20:])/20 if len(vo)>=20 and all(v is not None for v in vo[-20:]) else None
    support=round(min(lows),2) if len(lows)==10 and all(v is not None for v in lows) else None; resistance=round(max(highs),2) if len(highs)==20 and all(v is not None for v in highs) else None
    st='整理'
    if ms['20'] and ms['60']:st='多頭排列／偏強' if px>ms['20']>ms['60'] else ('空頭排列／偏弱' if px<ms['20']<ms['60'] else ('站回月線／整理偏強' if px>ms['20'] else '月線下方／整理偏弱'))
    sr={'support':support,'resistance':resistance,
      'support_valid':f'回測 {support} 附近止跌，收盤未有效跌破，且未出現明顯放量破位' if support is not None else 'Unavailable',
      'support_invalid':f'收盤有效跌破 {support}；若同步放量，支撐失效確認度提高' if support is not None else 'Unavailable',
      'resistance_valid':f'接近 {resistance} 無法收盤站穩，或突破後迅速跌回，壓力仍有效' if resistance is not None else 'Unavailable',
      'resistance_invalid':f'帶量突破且收盤站穩 {resistance}，後續回測不破，原壓力可視為轉支撐' if resistance is not None else 'Unavailable',
      'basis':'支撐採近10交易日低點；壓力採近20交易日高點。以收盤確認為主，盤中刺穿不單獨判定失效。'}
    return {'status':'ok' if len(h)>=20 else '資料不足','as_of':h[-1]['date'],'price_basis':'官方日K收盤；非MIS現價','history_days':len(h),'group':stock_group(c),'ma':ms,'support':support,'resistance':resistance,
      'support_resistance':sr,'volume_ratio20':round(vo[-1]/av,2) if av else None,'state':st,
      'note':'未復權歷史價格，除權息可能造成跳空。技術現況描述，不預測漲跌；支撐／壓力為動態區域，需隨每日K線更新。'}

def peer_snapshot(c,limit=6):
    out=[]
    for p in [x for x in company_rows() if x['code']!=c['code'] and x.get('industry')==c.get('industry')][:30]:
        v=valuation_row(p['code'],p['market']);q=None
        try:q=mis_quote(p['code'],p['market'])
        except:pass
        if v or q:out.append({'code':p['code'],'name':p['name'],'price':(q or {}).get('price'),'pe':(v or {}).get('pe'),'pb':(v or {}).get('pb'),'yield':(v or {}).get('yield')})
        if len(out)>=limit:break
    return out
def _company_by_code(code):
    return company_by_code_fast(code)

@app.get('/api/stock/summary')
def stock_summary():
    code=request.args.get('code','').strip(); c=_company_by_code(code)
    if not c:return jsonify({'ok':False,'error':'stock code not found'}),404
    v=valuation_row(code,c['market'],wait=False)
    q,e=safe_call(lambda:mis_quote(code,c['market']))
    vs=_VALUATION_CACHE.status(c['market'])
    return jsonify({'ok':True,'company':{**{k:v for k,v in c.items() if k!='raw'},'group':stock_group(c)},
        'quote':q,'valuation':v,'status':'OK',
        'valuation_status':{k:'OK' if v and v.get(k) is not None else ('Loading' if vs=='Loading' else 'Unavailable') for k in ('pe','pb','yield')},
        'source_status':{'公司':'OK','行情':'OK' if q and q.get('price') is not None else 'Unavailable',
            '估值':'OK' if v and any(v.get(k) is not None for k in ('pe','pb','yield')) else ('Loading' if vs=='Loading' else 'Unavailable')},
        'errors':[x for x in [e,_QUOTE_CACHE.error(code),_VALUATION_CACHE.error(c['market'])] if x]})

def _history_pending(c):
    data=history_result(c['code'],c['market'])
    return data, _HISTORY_CACHE.status((c['market'],c['code'],12))

@app.get('/api/stock/technical')
def stock_technical():
    code=request.args.get('code','').strip(); c=_company_by_code(code)
    if not c:return jsonify({'ok':False,'error':'stock code not found'}),404
    data,status=_history_pending(c)
    if data is None:return jsonify({'ok':True,'technical':None,'status':status})
    t,e=safe_call(lambda:technical_snapshot(c,None),{'status':'資料不足'})
    return jsonify({'ok':True,'technical':t,'status':'OK' if t['status']=='ok' else 'Unavailable','errors':data['errors'],'error':e})

@app.get('/api/stock/model')
def stock_model():
    code=request.args.get('code','').strip(); c=_company_by_code(code)
    if not c:return jsonify({'ok':False,'error':'stock code not found'}),404
    data,status=_history_pending(c)
    _valuation_rows(c['market'],wait=False)
    _INSTITUTIONAL_CACHE.get(c['market'],lambda:_institutional_rows(c['market']),wait=False)
    if status=='Loading' or any(cache.status(c['market'])=='Loading' for cache in (_VALUATION_CACHE,_INSTITUTIONAL_CACHE)):
        return jsonify({'ok':True,'analysis':None,'status':'Loading'})
    q,_=safe_call(lambda:mis_quote(code,c['market'])); a,e=safe_call(lambda:analyst_model(c,q))
    usable=a and (a['overnight']['status']!='資料不足' or a['swing']['status']!='資料不足')
    return jsonify({'ok':True,'analysis':a,'status':'OK' if usable else 'Unavailable',
        'errors':(data or {}).get('errors',[])+[x for x in [_INSTITUTIONAL_CACHE.error(c['market']),_VALUATION_CACHE.error(c['market'])] if x], 'error':e})

@app.get('/api/stock/peers')
def stock_peers():
    code=request.args.get('code','').strip(); c=_company_by_code(code)
    if not c:return jsonify({'ok':False,'error':'stock code not found'}),404
    candidates=[x for x in company_rows() if x['code']!=code and c.get('industry') and x.get('industry')==c['industry']][:8]
    out=[]; loading=False
    for x in candidates:
        v=valuation_row(x['code'],x['market'],wait=False)
        loading=loading or _VALUATION_CACHE.status(x['market'])=='Loading'
        if v and any(v.get(k) is not None for k in ('pe','pb','yield')):
            out.append({'code':x['code'],'name':x['name'],'market':x['market'],'group':stock_group(x),**v})
    return jsonify({'ok':True,'rows':out[:5],'status':'Loading' if loading else ('OK' if out else 'Unavailable'),
                    'basis':'相同官方產業分類，非完全相同產品；估值日期請逐列確認'})

@app.get('/api/stock/research')
def stock_research():
    code=request.args.get('code','').strip(); c=_company_by_code(code)
    if not c:return jsonify({'ok':False,'error':'stock code not found'}),404
    q,_=safe_call(lambda:mis_quote(code,c['market']))
    return jsonify({'ok':True,'version':'9.2.3','company':{k:v for k,v in c.items() if k!='raw'},'quote':q,'note':'heavy modules load independently'})

def _warm_company_cache():
    while True:
        try: _install_company_rows(_load_company_rows())
        except Exception as exc: app.logger.warning('company warmup: %s',exc)
        time.sleep(_COMPANY_TTL if company_rows() else 60)

if os.getenv('COMPANY_WARMUP','1') != '0':
    threading.Thread(target=_warm_company_cache,daemon=True,name='company-master').start()

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','8787')),debug=False,threaded=True)
