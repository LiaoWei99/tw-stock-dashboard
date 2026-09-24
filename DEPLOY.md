V9.2 公司資料快取／搜尋修復
- 公司 master data 加 6 小時記憶體 TTL cache
- 啟動後背景預熱，不阻塞 Render 開機
- 已有 cache 時外部 TWSE/TPEx 暫時失敗會沿用 stale cache
- 股票代號搜尋走 fast path；公司 master 不可用時改用 MIS quote fallback
- /api/company/search 不再每次搜尋都同步重新下載完整公司清單
- AbortError 轉成「資料來源回應逾時」，不再顯示 signal is aborted without reason
- 保留 V9.1 模組拆分，單一模組失敗不拖垮整頁
