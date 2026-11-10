# 安全性與隱私

## 回報漏洞

請勿在公開 issue 張貼漏洞細節。請透過 GitHub 的 Private vulnerability reporting（Security → Report a vulnerability）回報。

## 資料處理

- 所有提取與模型推論預設在本機執行，不上傳設計檔，不使用付費 API。
- 網路連線只發生在使用者明確執行的安裝或下載指令（pip、`ali models fetch`、OCR 首次 `download_models`）。
- 產生的 JSX 腳本會在你的 Adobe 應用程式內讀寫檔案；執行前請檢查腳本與輸出路徑。腳本設計為拒絕覆寫既有檔案。
- 若未來提供雲端推論，必須是明確選擇加入，並另行公布資料保存與刪除政策。
