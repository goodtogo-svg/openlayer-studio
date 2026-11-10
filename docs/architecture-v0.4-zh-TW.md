# OpenLayer Studio v0.4 架構

v0.4 新增四個獨立 worker 引擎；原本 `process`、`validate`、`fonts`、`adobe` 與 `ali/1` manifest 保持相容。**原本 `process --upscale` 仍是 Lanczos；真正 AI 超解析需使用 `engine sr`。**

## 資料流

```text
來源檔（只讀、SHA256）→ v0.3 ingestion → ali/1 manifest
                                ↓
                    PDF 原始文字優先 → 不需 OCR
                                ↓
             每頁串流 → 獨立 engine subprocess
                segmentation / ocr / fonts / sr
                                ↓
             state.json + result.json + inference.log
                                ↓
           ali-scene/1 → quality-report.json → scene-adobe
                                ↓
                   JSX → 實際 Adobe 儲存、重新開啟
```

## 環境與資源

- 專案 `.venv` 只負責 CLI、現有抽取與測試。四個環境位於 `models/envs/{segmentation,ocr,fonts,sr}`；不調整系統 Python。
- `models/locks/*.txt` 是直接依賴版本，`*-resolved.txt` 是本機成功安裝後完整 freeze。setup 腳本優先使用 resolved lock。Tiny/Small 共用 SAM 版本環境，每次以獨立 worker 載入其中一個模型。
- `ali models list` 顯示 checkpoint 是否存在且符合本機完整性紀錄；`ali models fetch NAME` 明確下載官方模型。未取得權重不可成功。
- 首次下載記錄 URL、SHA256 與授權資訊；這是首次信任後的完整性驗證，**不是官方簽章或供應鏈安全證明**。OCR 另以語言 receipt 鎖定下載檔案。開源字型需要明確授權檔與 SHA256。
- `ali hardware` 回報系統、arm64 與記憶體；沙箱可能禁止 sysctl，此時 memory 為 null。Torch worker 自行檢查 MPS，否則 CPU。OCR 與字型使用 CPU。
- SAM auto 若遇 MPS 未支援算子會記錄錯誤後完整改用 CPU；明確要求 mps 時不偽稱成功。SR 支援 CPU 後備，並記錄原因。
- 一頁一模型，避免同時載入所有權重。引擎輸入上限 16 MP，閉式 Matting 上限 1 MP，SR 輸出上限 64 MP；SR 預設 128px tile、16px overlap。這些是容量護欄，不是動態剩餘記憶體預測器。
- `result.json` 記錄推論秒數、worker peak RSS、backend 與品質狀態。RSS 不涵蓋完整 Metal GPU 記憶體，不能當總統一記憶體上限證明。

## 可恢復任務

`ali engine ENGINE INPUT --out NEW_JOB --options JSON` 建立 request、state、log 與 result。options 可寫成 `@檔案.json`。狀態為 running／complete／failed；失敗返回非零，沒有結果不會被當成功。

使用 `--resume` 時，來源 hash、選項與外部 prompt/index hash 必須一致。已完成且產物 hash 正確的任務可直接回傳；產物遭修改則重新推論。`.running` PID lock 防止同一 job 併行；明確 resume 可辨識已不存在的 PID。無法判定 PID 或活躍程序時不任意移除 lock。PID 重用及整機斷電後的人工排查仍需操作員處理。

模型或程式版本變更應建立新 job；目前恢復 fingerprint **未包含整份程式碼與模型環境版本**，不可跨版本盲目沿用成功結果。resume 會重跑失敗引擎，不支援模型內部 tile 級 checkpoint。

## 四個引擎

### SAM 2.1

Tiny／Small 的真實 SAM checkpoint，支援 prompt 與無提示 AMG。遮罩轉出 PNG、alpha layer，從 erode/dilate trimap 執行 PyMatting closed-form alpha。這是估計 Alpha；頭髮、玻璃與半透明品質尚無基準證明。

背景使用 OpenCV Telea 補全，標記 `is_original=false`、`inferred_opencv_telea_not_original`，**不是生成式背景模型，也不是原稿真實背景**。圖層順序僅是啟發式，待人工確認。

提示資料使用 `ali-prompts/1`：

```json
{
  "schema": "ali-prompts/1",
  "coordinate_space": "source_pixels",
  "objects": [{
    "id": "object-1",
    "points": [[80,100]], "labels": [1],
    "box": [25,35,135,185],
    "corrections": [{"operation":"subtract","polygon":[[30,40],[40,40],[40,50]]}]
  }]
}
```

points 標籤 1=前景、0=背景；box 為 x1,y1,x2,y2。多邊形校正為 add/subtract。格式供未來介面使用，**本次沒有新增互動標註 GUI**。

### PaddleOCR 3.x

支援 zh-TW／zh-CN／en／ja／ko。首次 `options.download_models=true` 才允許下載官方模型；之後以明確本機模型目錄執行，避免 PaddleX 即使有快取仍因離線 host 檢查而失敗。

輸出 text、polygon、angle_degrees、confidence、language、文字區域 mask。language 是使用者選擇辨識器，**不是自動語言分類**。角度從辨識框估計，不是任意角度文字排版恢復。PDF 有原始可抽取文字即保留其內容、座標與字型，跳過整頁 OCR；混合可選文字與掃描區塊的局部 OCR 尚未實作。

### 免費字型視覺比對

`font-index` 只讀明確 catalog 中附授權檔的 OFL-1.1／Apache-2.0 候選，不把 macOS 內建商業字型當開源候選。索引包含字元覆蓋、字重、variable axes 與雜湊。

`engine fonts` 需要使用者確認的文字、裁切文字圖與 index。以共同 baseline 渲染、不同字距搜尋、字形正規化 binary IoU 排序，產生最多 5 個字型 face 與預覽。字重由候選 face 比較推估；原始點數未知、variable axes 連續搜尋與複雜 shaping／透視校正尚未支援。分數不是機率或字型身分證明。`selected_font` 保留人工選擇，未選時 needs_human_confirmation=true。

### Real-ESRGAN

載入官方 RealESRGAN_x4plus 或 anime_6B checkpoint，使用 BasicSR RRDBNet 的原生 4× RGB 輸出。不是普通 resize。照片使用 23-block，插畫使用 6-block；透明素材先填補無定義的透明 RGB，再執行照片模型。Alpha 用 bilinear coverage 保留，**沒有宣稱 Alpha 本身是 AI 推論**。

有 padding overlap 與線性權重融合、CPU 後備、尺寸與 tile 防呆。推論可能產生不真實細節；合成圖尺寸通過不等於照片修復品質通過。

## Scene Graph 與限制

`ali-scene/1` 保存 boards、nodes、座標系統、visible、z_index、provenance、quality；另有 sidecar，不改 ali/1 的語意。type 包含 raster、vector、text。來源文字／OCR 先隱藏以免覆疊原 raster；只有人工核准字型及版面後才設定 visible、human_approved、approved_font。

目前沒有自動從 OCR 裁字、選字型、移除原文字並逐區重建整份刊物的閉環；Scene Graph 是接續人工校正與重建的介面，**不是原刊物已可編輯重建成功的證據**。參考 Adobe 驗證文件。
