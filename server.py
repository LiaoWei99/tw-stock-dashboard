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
def health(): return jsonify({'ok':True,'service':'tw-stock-dashboard','version':'6.0-dual-model-scoring'})

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

# --- V6 analyst scoring layer ---
def _pick(r, includes):
    for k,v in r.items():
        if all(s in k for s in includes): return v
    return None

def valuation_row(code):
    try:
        for r in get_json('https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL'):
            if str(r.get('Code','')).strip()==code:
                return {'pe':n(r.get('PEratio')) or None,'pb':n(r.get('PBratio')) or None,'yield':n(r.get('DividendYield')) or None,'source':'TWSE BWIBBU_ALL'}
    except Exception: pass
    return None

def institutional_row(code):
    try:
        for r in get_json('https://openapi.twse.com.tw/v1/fund/T86_ALL'):
            if str(r.get('Code','')).strip()!=code: continue
            def val(*names):
                for name in names:
                    if name in r:return n(r.get(name))
                for k,v in r.items():
                    if any(name in k for name in names):return n(v)
                return 0
            foreign=val('Foreign_Investor','Foreign Investors','外陸資買賣超股數','外資及陸資買賣超股數')
            trust=val('Investment_Trust','Investment Trust','投信買賣超股數')
            dealer=val('Dealer_total','Dealer','自營商買賣超股數')
            total=val('Total','三大法人買賣超股數') or foreign+trust+dealer
            return {'foreign':foreign,'trust':trust,'dealer':dealer,'total':total,'source':'TWSE T86_ALL','timing':'官方盤後法人資料；依交易所公告時程更新'}
    except Exception: pass
    return None

def twse_history(code, months=4):
    # Listed-stock daily history. Failure returns [] rather than inventing data.
    import datetime
    out=[]; today=datetime.date.today(); seen=set()
    for i in range(months):
        y=today.year; m=today.month-i
        while m<=0: y-=1; m+=12
        url='https://www.twse.com.tw/exchangeReport/STOCK_DAY?'+urlencode({'response':'json','date':f'{y:04d}{m:02d}01','stockNo':code})
        try:
            j=get_json(url)
            for row in j.get('data',[]):
                if len(row)<9: continue
                ds=str(row[0]); close=n(row[6]); vol=n(row[1]); op=n(row[3]); hi=n(row[4]); lo=n(row[5])
                if ds not in seen and close:
                    seen.add(ds); out.append({'date':ds,'open':op,'high':hi,'low':lo,'close':close,'volume':vol})
        except Exception: pass
    return list(reversed(out))

def _norm(points, available, target):
    return round(points/available*target) if available>0 else None

def analyst_model(company, quote):
    code=company['code']; market=company['market']; val=valuation_row(code) if market=='上市' else None
    inst=institutional_row(code) if market=='上市' else None
    hist=twse_history(code,4) if market=='上市' else []
    # Fundamental: 20 overnight / 35 swing. Only verified fields count.
    fp=0; fa=0; freasons=[]
    if company.get('industry'): fp+=4; fa+=4; freasons.append('官方產業分類可辨識')
    if company.get('business'): fp+=4; fa+=4; freasons.append('主要業務資料可辨識')
    if val:
        fa+=12
        pe=val.get('pe'); pb=val.get('pb'); dy=val.get('yield')
        if pe and 0<pe<=25: fp+=5; freasons.append('本益比處於較低區間')
        elif pe and pe<=50: fp+=3; freasons.append('本益比中性')
        elif pe: fp+=1; freasons.append('本益比較高，估值風險需留意')
        if pb and pb<=3: fp+=3
        elif pb and pb<=6: fp+=2
        elif pb: fp+=1
        if dy is not None: fp+=1
    # Technical: current quote + history when available.
    tp=0; ta=0; treasons=[]
    if quote and quote.get('price'):
        ta+=18; pct=quote.get('pct') or 0; op=quote.get('open'); hi=quote.get('high'); lo=quote.get('low'); px=quote['price']
        if pct>0: tp+=5
        if pct>=2: tp+=2
        if op and px>=op: tp+=4; treasons.append('價格守在開盤價之上')
        if hi and lo and hi>lo:
            p=(px-lo)/(hi-lo)
            if p>=.8: tp+=5; treasons.append('價格位於日內區間上緣')
            elif p>=.55: tp+=3
            else: tp+=1
        tp+=2
    ma20=ma60=ret20=avgvol20=None
    if len(hist)>=20:
        closes=[x['close'] for x in hist]; vols=[x['volume'] for x in hist]
        ma20=sum(closes[-20:])/20; avgvol20=sum(vols[-20:])/20
        ret20=(closes[-1]/closes[-20]-1)*100 if closes[-20] else None
        ta+=12
        if closes[-1]>ma20: tp+=5; treasons.append('收盤在20日均線之上')
        if ret20 is not None and ret20>0: tp+=3
        if avgvol20 and vols[-1]>avgvol20: tp+=2; treasons.append('成交量高於20日均量')
        tp+=1
    if len(hist)>=60:
        ma60=sum(x['close'] for x in hist[-60:])/60; ta+=10
        if hist[-1]['close']>ma60: tp+=4
        if ma20 and ma20>ma60: tp+=4; treasons.append('20日均線高於60日均線')
        tp+=1
    # Chip: official institutional flow when available.
    cp=0; ca=0; creasons=[]
    if inst:
        ca=40; total=inst['total']; foreign=inst['foreign']; trust=inst['trust']
        if total>0: cp+=16; creasons.append('三大法人合計買超')
        elif total==0: cp+=8
        if foreign>0: cp+=10; creasons.append('外資買超')
        elif foreign==0: cp+=5
        if trust>0: cp+=10; creasons.append('投信買超')
        elif trust==0: cp+=5
        cp+=4
    # Overnight adjusted component scores and completeness.
    f20=_norm(fp,fa,20); t40=_norm(tp,ta,40); c40=_norm(cp,ca,40)
    avail_o=(20 if f20 is not None else 0)+(40 if t40 is not None else 0)+(40 if c40 is not None else 0)
    total_o=sum(x for x in [f20,t40,c40] if x is not None)
    comp_o=round((fa/20*20 if fa else 0)+(ta/40*40 if ta else 0)+(ca/40*40 if ca else 0))
    # Keep completeness strict; recommendation requires >=60% and technical/chip evidence.
    overnight_ok=comp_o>=60 and total_o>=65 and t40 is not None and t40>=24 and c40 is not None and c40>=20
    # Swing 35/35/30 reweight from same verified evidence; history is required.
    f35=_norm(fp,fa,35); t35=_norm(tp,ta,35); c30=_norm(cp,ca,30)
    total_s=sum(x for x in [f35,t35,c30] if x is not None)
    comp_s=round((fa/20*35 if fa else 0)+(ta/40*35 if ta else 0)+(ca/40*30 if ca else 0))
    swing_ok=(not overnight_ok) and len(hist)>=20 and comp_s>=60 and total_s>=62 and t35 is not None and t35>=20
    # Price plan only when enough daily history exists.
    plan=None
    if swing_ok and quote and quote.get('price') and len(hist)>=20:
        recent=hist[-20:]; support=max(min(x['low'] for x in recent[-10:] if x['low']), ma20*0.97 if ma20 else 0)
        entry_low=max(support, (ma20 or quote['price'])*0.985); entry_high=max(entry_low, (ma20 or quote['price'])*1.015)
        stop=min(entry_low*0.965, support*0.985) if support else entry_low*.96
        risk=max(entry_high-stop, entry_high*.02); resistance=max(x['high'] for x in recent if x['high'])
        target1=max(resistance, entry_high+1.5*risk); target2=entry_high+2.2*risk
        trend='10–20個交易日' if ma60 and ma20 and ma20>ma60 else '5–10個交易日'
        plan={'holding':trend,'entry':[round(entry_low,2),round(entry_high,2)],'stop':round(stop,2),'target1':round(target1,2),'target2':round(target2,2),'rr1':round((target1-entry_high)/risk,2),'basis':'20日均線、近10日低點、近20日壓力與RR≥1.5；價格觸及失效條件優先於持有天數'}
    return {
      'method':'規則式研究評分，不代表保證報酬或個人化投資建議',
      'fundamental':{'overnight_score':f20,'swing_score':f35,'available_points':fa,'reasons':freasons,'valuation':val},
      'technical':{'overnight_score':t40,'swing_score':t35,'available_points':ta,'reasons':treasons,'ma20':round(ma20,2) if ma20 else None,'ma60':round(ma60,2) if ma60 else None,'return20':round(ret20,2) if ret20 is not None else None},
      'chips':{'overnight_score':c40,'swing_score':c30,'available_points':ca,'reasons':creasons,'institutional':inst},
      'overnight':{'score':total_o,'completeness':min(100,comp_o),'eligible':overnight_ok,'threshold':'完整度>=60、總分>=65、技術>=24/40、籌碼>=20/40'},
      'swing':{'score':total_s,'completeness':min(100,comp_s),'eligible':swing_ok,'threshold':'隔日沖不符合後，至少20日歷史、完整度>=60、總分>=62、技術>=20/35','plan':plan},
      'missing':[x for x,ok in [('估值/基本面量化',bool(val)),('20日以上歷史K線',len(hist)>=20),('三大法人籌碼',bool(inst))] if not ok]
    }

@app.get('/api/stock/analysis')
def stock_analysis():
    code=request.args.get('code','').strip()
    if not code:return jsonify({'ok':False,'error':'code required'}),400
    try:
        matches=[x for x in company_rows() if x['code']==code]
        if not matches:return jsonify({'ok':False,'error':'stock code not found'}),404
        c=matches[0]; q=None
        try:q=mis_quote(code,c['market'])
        except Exception: pass
        a=analyst_model(c,q)
        return jsonify({'ok':True,'company':{k:v for k,v in c.items() if k!='raw'},'quote':q,'analysis':a,'ts':int(time.time()*1000)})
    except Exception as e:return jsonify({'ok':False,'error':str(e)}),502
