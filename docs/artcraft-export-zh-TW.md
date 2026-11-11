# ArtCraft 2D 場景匯出

`ali scene-artcraft scene.json --out NEW_DIR` 把 `ali-scene/1` 每個 board 的可見 raster 圖層，轉成 [ArtCraft](https://github.com/storytold/artcraft)（MIT／Apache-2.0）2D 編輯器的場景 JSON（pagedraw `version: "2.0"`）。

格式依據：ArtCraft `frontend/libs/components/pagedraw/src/lib/stores/SceneState.ts` 的 `exportSceneAsJson`／`importSceneFromJson`，核對版本為 commit `55e6e5c`（2026-11-10）。ArtCraft 改版後須重新核對。

## 輸出

- `page_NNN.artcraft-scene.json`：`drawNodes` 依 z_index 由下到上排列，每層是 `type: "image"` 節點，影像以 PNG data URL 內嵌於 `imageDataUrl`。
- `artcraft-handoff.json`：每頁的畫布比例、縮放倍率、留白位移、匯出與略過的節點，以及 `artcraft_verified: false`。

## 限制

1. ArtCraft 畫布只有 683×1024、1024×683、1024×1024 三種；依來源比例選最接近的一種，等比縮小置中，不放大。這是給 AI 生成用的低解析交接，不是印刷級輸出。
2. ArtCraft 2D 編輯器沒有文字與向量節點；可見的 text／vector 節點會略過並記錄原因。
3. 圖層須為整板大小，或附 `bbox`（像素座標）；否則拒絕匯出。
4. 推估圖層的 provenance 只保存在 handoff 檔，ArtCraft 內看不到。
5. 截至上述 commit，ArtCraft 介面尚未提供「從檔案載入場景」的按鈕（`loadSceneFromFile` 已實作但未接到 UI）。在 ArtCraft 實際載入成功前，不得宣告匯入成功。
