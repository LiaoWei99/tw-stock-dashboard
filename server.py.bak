from flask import Flask, jsonify, send_from_directory, request
from urllib.request import Request, urlopen
from urllib.parse import quote, urlencode
import json, os, time, xml.etree.ElementTree as ET, re
app=Flask(__name__, static_folder='.')
BASE=os.path.dirname(__file__)

def fetch(url, accept='application/json'):
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 TW-Stock-Dashboard/3.0','Accept':accept})
    with urlopen(req,timeout=15) as r: return r.read()
def get_json(url): return json.loads(fetch(url).decode('utf-8'))
def n(v):
    try:
        x=str(v or '').replace(',','').replace('+','').strip()
        return float(x) if x not in ('','--','---') else 0.0
    except:return 0.0

def company_rows():
    out=[]
    for market, dataset in [('上市','opendata/t187ap03_L'),('上櫃','opendata/t187ap03_O')]:
        try:
            for r in get_json('https://openapi.twse.com.tw/v1/'+dataset):
                code=next((str(r.get(k,'')).strip() for k in r if '公司代號' in k or k.lower() in ('code','companycode')), '')
                name=next((str(r.get(k,'')).strip() for k in r if '公司簡稱' in k), '') or next((str(r.get(k,'')).strip() for k in r if '公司名稱' in k), '')
                industry=next((str(r.get(k,'')).strip() for k in r if '產業別' in k), '')
                business=next((str(r.get(k,'')).strip() for k in r if '主要經營業務' in k or '主要業務' in k), '')
                if code: out.append({'code':code,'name':name,'industry':industry,'business':business,'market':market,'raw':r})
        except Exception:
            pass
    return out

def mis_quote(code, market):
    ex='tse' if market=='上市' else 'otc'
    url='https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch='+quote(f'{ex}_{code}.tw',safe='|_.')+'&json=1&delay=0'
    data=get_json(url); rows=data.get('msgArray') or []
    if not rows:return None
    r=rows[0]; last=n(r.get('z')) or n(r.get('y')); prev=n(r.get('y')); change=last-prev if last and prev else 0
    return {'code':code,'name':r.get('n') or r.get('nf') or '', 'market':market,'price':last or None,'prev_close':prev or None,
      'change':round(change,2),'pct':round(change/prev*100,2) if prev else None,'open':n(r.get('o')) or None,'high':n(r.get('h')) or None,
      'low':n(r.get('l')) or None,'volume':n(r.get('v')) or None,'time':r.get('t') or '', 'date':r.get('d') or '', 'source':'TWSE MIS'}

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
def health(): return jsonify({'ok':True,'service':'tw-stock-dashboard','version':'5.0-search-quote-news-sentiment'})

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
            value=n(r.get('TradeValue'));vol=n(r.get('TradeVolume'));close=n(r.get('ClosingPrice'));change=n(r.get('Change'));prev=close-change if close else 0
            out.append({'code':code,'name':r.get('Name',''),'close':close,'change':change,'pct':round(change/prev*100,2) if prev else 0,'value':value,'volume':vol,'open':n(r.get('OpeningPrice')),'high':n(r.get('HighestPrice')),'low':n(r.get('LowestPrice')),'vwap':round(value/vol,2) if vol else None})
        out.sort(key=lambda x:x['value'],reverse=True)
        return jsonify({'ok':True,'source':'TWSE OpenAPI STOCK_DAY_ALL','timing':'official dataset; not tick-real-time','rows':out[:limit],'ts':int(time.time()*1000)})
    except Exception as e:return jsonify({'ok':False,'error':str(e)}),502

@app.get('/api/company/search')
def company_search():
    q=request.args.get('q','').strip()
    if not q:return jsonify({'ok':False,'error':'q required'}),400
    try:
        rows=company_rows(); ql=q.lower()
        if q.isdigit():
            hits=[x for x in rows if x['code']==q]
        else:
            exact=[x for x in rows if x['name'].lower()==ql]
            fuzzy=[x for x in rows if ql in x['name'].lower() or ql in x['industry'].lower() or ql in x['business'].lower()]
            seen=set(); hits=[]
            for x in exact+fuzzy:
                k=(x['market'],x['code'])
                if k not in seen: seen.add(k); hits.append(x)
        return jsonify({'ok':True,'source':'TWSE OpenAPI 公司基本資料','match':'exact-code' if q.isdigit() else 'name-industry-business','rows':hits[:20]})
    except Exception as e:return jsonify({'ok':False,'error':str(e)}),502

@app.get('/api/stock/context')
def stock_context():
    code=request.args.get('code','').strip()
    if not code:return jsonify({'ok':False,'error':'code required'}),400
    try:
        matches=[x for x in company_rows() if x['code']==code]
        if not matches:return jsonify({'ok':False,'error':'stock code not found'}),404
        c=matches[0]
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

@app.get('/api/news/stock')
def news_stock():
    code=request.args.get('code','').strip(); name=request.args.get('name','').strip(); industry=request.args.get('industry','').strip()
    terms=' OR '.join(x for x in [code,name,industry] if x)
    if not terms:return jsonify({'ok':False,'error':'code/name/industry required'}),400
    try:
        rows=news_rss(f'({terms}) when:7d',15)
        for x in rows:
            x['sentiment'],x['sentiment_reason']=sentiment(x.get('title',''))
        return jsonify({'ok':True,'source':'Google News RSS 聚合','rows':rows,'query':terms,'sentiment_note':'多空為標題規則初判，不代表股價預測；中性/待確認不強行分類。'})
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

if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.getenv('PORT','8787')),debug=False)
