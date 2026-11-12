# 圖片 → 向量重建（`ali vector-rebuild`）

把一張平面圖（PNG/JPG）自動分析後重建成**向量**稿：圖形是形狀與漸層，文字是可編輯的即時文字。全程在本機或雲端執行，不需要開 Illustrator。

```bash
ali vector-rebuild poster.png --out jobs/poster-vector
# 指定字型（可重複）：檔案路徑，或 檔案=PostScript 名稱，或 variable:可變字型
ali vector-rebuild banner.png --out jobs/banner-vector \
  --font variable:models/fonts/NotoSansTC.ttf \
  --font /usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf=Arial-BoldMT
```

需要 `segmentation` 與 `ocr` 兩個隔離環境（`scripts/setup-v04.py`）以及 `sam2-small` 權重。補字字典需要在 `segmentation` 環境另裝 `wordfreq`（選配）。

## 兩種方法（`--method`）

- `scene`：把海報當成設計物件重建（背景漸層、主標、膠帶、緞帶、層序、陰影、材質），也就是 11/08 兩張膠帶海報驗收通過的做法，改成自動量測。紀錄與參數見 [場景重建](scene-rebuild-zh-TW.md)。
- `regions`：分塊分割 + 形狀辨識（下方流程），適合有圖示、徽章、照片的版面。
- `auto`（預設）：先跑 `scene`，成品與原圖平均誤差 ≤ 25 就採用，否則改用 `regions`。

以下「流程」說明 `regions` 方法。

## 流程

1. **OCR**（PP-OCRv5，`ocr` 環境）取得文字行；`--locale auto` 依內容判斷中文或英文。
2. **分塊 SAM 2.1**：以約 520 px 的方塊、100 px 重疊在原解析度取樣，跨方塊的遮罩合併、去重後畫成互不重疊的區域圖。
3. **文字擬合**：以字型外框量測，搜尋字型 × 斜度 × 字距，把字的墨水框非等比對齊量到的墨水框（與先前人工驗收通過的重建規則相同）。旋轉的文字（膠帶、貼紙）在旋轉座標內擬合；多色字分成色段；被其他圖形遮住的文字會忽略被遮住的像素、放到該圖形的下層。清楚的描邊與投影會變成描邊路徑，柔和陰影不會。
4. **徽章弧形字**：近圓形區域展開成長條再 OCR，逐字沿弧排列。
5. **背景**：自動選水平、垂直或放射狀的多段漸層。
6. **形狀辨識**：每個區域依序嘗試圓／橢圓、多邊形（3–10 點）、平滑貝茲；填色為平色、線性或放射漸層。顏色無法用模型表達的區域才用 vtracer 多色描圖。
7. **輸出**：`rebuild.svg`（具名圖層群組與 `<text>`）、`rebuild.pdf`、`rebuild.ai`（PDF 相容 .ai）、`rebuild-report.json`（每行文字的擬合分數與決策、區域統計、與原圖的 MAE）。

## 圖層

| 圖層 | 內容 |
|---|---|
| `01_Background` | 背景漸層 |
| `01a_Art_under_text` / `01b_Text_behind_art` | 只在有文字被圖形遮住時出現 |
| `02_Group_nn` | 相鄰的形狀群組 |
| `03_Lines_and_dots` | 背景上的細線與光點 |
| `04_Text_decorations` | 文字描邊／投影 |
| `05_Live_text` | 可編輯文字 |

## 限制

- 結果是**未經使用者驗收**的草稿；`.ai` 必須在 Illustrator 開啟、存檔、再開啟確認後才算原生格式成功（AGENTS.md）。
- PDF 相容 .ai 的圖層在 Illustrator 中沒有名稱；具名圖層請用 SVG。
- 擬合分數低於門檻的文字保留為圖形（報告中標為 `kept_as_artwork_poor_fit`）。OCR 沒抓到的字（例如破折號）不會出現。
- 字型只會從提供的候選字型中選；原稿字型不在其中時，以非等比縮放與斜度近似。
