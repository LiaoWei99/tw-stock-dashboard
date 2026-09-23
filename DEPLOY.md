# 手機即時版部署

此專案必須部署在 HTTPS 公網主機後，手機才能在外網開啟並即時向後端取得資料。

## 啟動
`pip install -r requirements.txt`
`gunicorn -b 0.0.0.0:${PORT:-8787} server:app`

## 即時資料
- `/api/twse/realtime`：由伺服器代理 TWSE MIS，避免瀏覽器 CORS。
- `/api/twse/openapi/...`：TWSE OpenAPI。
- `/api/taifex/status`：TAIFEX 官方資料狀態；TAIFEX 官方網站即時頁面不提供一般程式 API。
- `/api/quote-provider/quotes`：授權即時行情商接口。

若要 TX 逐筆即時，伺服器設定：
`REALTIME_PROVIDER_URL`
`REALTIME_PROVIDER_TOKEN`
`REALTIME_PROVIDER_NAME`

Token 不可放在 HTML/JavaScript。

## iPhone
部署後用 Safari 開啟 HTTPS 網址 → 分享 → 加入主畫面，即可像 App 使用。
頁面每 15 秒更新股票即時行情；切回頁面後可按「更新行情」立即刷新。
