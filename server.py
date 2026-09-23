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
    try:return float(str(v).replace(',',''))
    except:return 0.0

@app.get('/')
@app.get('/live.html')
def home(): return send_from_directory(BASE,'live.html')
@app.get('/manifest.webmanifest')
def manifest(): return send_from_directory(BASE,'manifest.webmanifest',mimetype='application/manifest+json')
@app.get('/sw.js')
def sw(): return send_from_directory(BASE,'sw.js',mimetype='application/javascript')
@app.get('/health')
def health(): return jsonify({'ok':True,'service':'tw-stock-dashboard','version':'4.0-swing-overnight'})

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
    q=request.args.get('q','').strip().lower()
    if not q:return jsonify({'ok':False,'error':'q required'}),400
    try:
        rows=get_json('https://openapi.twse.com.tw/v1/opendata/t187ap03_L'); hits=[]
        for r in rows:
            blob=' '.join(str(v) for v in r.values()).lower()
            if q in blob:
                code=next((str(r.get(k,'')) for k in r if '公司代號' in k or k.lower() in ('code','companycode')), '')
                name=next((str(r.get(k,'')) for k in r if '公司簡稱' in k or '公司名稱' in k), '')
                industry=next((str(r.get(k,'')) for k in r if '產業別' in k), '')
                hits.append({'code':code,'name':name,'industry':industry,'raw':r})
            if len(hits)>=12:break
        return jsonify({'ok':True,'source':'TWSE OpenAPI 上市公司基本資料','rows':hits})
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
    try:return jsonify({'ok':True,'source':'Google News RSS 聚合','rows':news_rss(f'({terms}) when:7d',15),'query':terms})
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
