# 台股即時選股作戰台 v3

Render 啟動：`gunicorn -b 0.0.0.0:${PORT:-8787} server:app`

內建：TWSE MIS、TWSE OpenAPI、Google News RSS 聚合、個股搜尋/產業/新聞雷達。

真正「期貨盤中即時漲跌排行」需合法授權行情源，Render 設定：
- `FUTURES_PROVIDER_URL`
- `FUTURES_PROVIDER_TOKEN`
- `FUTURES_PROVIDER_NAME`

未設定時前端會顯示 Realtime Unavailable，不會把 TAIFEX 盤後資料冒充即時。

新聞是事件雷達，標題關聯不等同因果；應點擊原始媒體核對。
