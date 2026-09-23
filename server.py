from flask import Flask, jsonify, send_from_directory, request
from urllib.request import Request, urlopen
import json, os, time
app=Flask(__name__, static_folder='.')
BASE=os.path.dirname(__file__)

def get_json(url):
    req=Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urlopen(req,timeout=12) as r: return json.loads(r.read().decode('utf-8'))

@app.get('/')
def home(): return send_from_directory(BASE,'live.html')

@app.get('/manifest.webmanifest')
def manifest(): return send_from_directory(BASE,'manifest.webmanifest',mimetype='application/manifest+json')

@app.get('/sw.js')
def sw(): return send_from_directory(BASE,'sw.js',mimetype='application/javascript')

@app.get('/health')
def health(): return jsonify({'ok':True,'service':'tw-stock-dashboard'})

@app.get('/api/twse/realtime')
def realtime():
    ex=request.args.get('ex','tse'); codes=request.args.get('codes','2330').split(',')[:30]
    chans='|'.join(f'{ex}_{c}.tw' for c in codes if c.isdigit())
    # TWSE MIS public market-display endpoint. Browser calls go through this server to avoid CORS.
    url='https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch='+chans+'&json=1&delay=0'
    try:
        data=get_json(url); return jsonify({'ok':True,'source':'TWSE MIS','data':data,'ts':int(time.time()*1000)})
    except Exception as e: return jsonify({'ok':False,'error':str(e),'source':'TWSE MIS'}),502

@app.get('/api/twse/openapi/<path:dataset>')
def twse_openapi(dataset):
    # dataset e.g. exchangeReport/STOCK_DAY_ALL
    url='https://openapi.twse.com.tw/v1/'+dataset
    try: return jsonify({'ok':True,'source':'TWSE OpenAPI','data':get_json(url)})
    except Exception as e: return jsonify({'ok':False,'error':str(e)}),502

@app.get('/api/taifex/status')
def taifex_status():
    # TAIFEX official pages/Open Data are authoritative for futures daily/institutional data.
    return jsonify({'ok':True,'source':'TAIFEX','mode':'official-daily','note':'Configure a licensed real-time futures feed for tick-level TX data. Official TAIFEX daily/institutional data remains the source of record.'})

@app.get('/api/quote-provider/status')
def provider_status():
    configured=bool(os.getenv('REALTIME_PROVIDER_URL') and os.getenv('REALTIME_PROVIDER_TOKEN'))
    return jsonify({'configured':configured,'provider':os.getenv('REALTIME_PROVIDER_NAME','licensed-feed'),'note':'Set REALTIME_PROVIDER_URL and REALTIME_PROVIDER_TOKEN on the server. Credentials are never stored in browser code.'})

@app.get('/api/quote-provider/quotes')
def provider_quotes():
    base=os.getenv('REALTIME_PROVIDER_URL'); token=os.getenv('REALTIME_PROVIDER_TOKEN')
    if not base or not token: return jsonify({'ok':False,'error':'Realtime provider not configured'}),503
    codes=request.args.get('codes','')
    sep='&' if '?' in base else '?'
    req=Request(base+sep+'codes='+codes,headers={'Authorization':'Bearer '+token,'User-Agent':'TW-Stock-Dashboard/1.0'})
    try:
        with urlopen(req,timeout=10) as r: return jsonify({'ok':True,'data':json.loads(r.read().decode())})
    except Exception as e: return jsonify({'ok':False,'error':str(e)}),502

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','8787')),debug=False)
