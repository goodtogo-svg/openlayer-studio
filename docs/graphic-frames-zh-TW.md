# 圖框分層與原始影像交付

`graphics-rebuild` 接續已驗收的文章 INDD，不重建文字。需要 `python -m pip install -e '.[graphics]'`，其中 pikepdf 鎖定 10.17.0。

```sh
ali graphics-rebuild approved.indd --background background-original-vectors.pdf --out jobs/new-graphics --classification classification.json
```

背景必須是與已驗收文字稿尺寸、頁序一致、已展平 Form 的 PDF。此入口生成素材、graphics-plan.json 和 build-graphics.jsx；不自動啟動 Adobe，也不表示原生檔已驗收。classification JSON 的 illustration_regions 以頁碼字串對應頁面 pt 座標矩形列表，例如 `{"illustration_regions":{"1":[[0,0,842,150]]}}`。這是人工指定語意分類，並非 AI 分類。

流程保留原圖樣本與 ICC，遮罩轉成 PNG alpha，依原稿裁切建立 InDesign 原生圖框。陰影和邊框保留為局部 PDF 並與照片群組。底圖刪除已分離影像及相應局部裝飾，避免移動照片後留下重複影像。複雜底色、線條、表格及特殊字型仍保留在向量 PDF 底圖，尚非全部原生可編輯圖元。

腳本須依授權在 InDesign 執行、儲存、重新開啟，再驗證：文字內容／框位／串接無變化、無溢排、字型與連結正常、圖框及框內影像範圍、移動群組與獨立裁切。圓形路徑須處理 PDF 隱式閉合與等價邊界浮點差；不可只核對 bounding box。

限制：目前針對直接 Image XObject、軸向放置與單一路徑裁切；不自動遞迴拆解任意 Form，旋轉／斜切影像明確拒絕。多重或複合裁切需專案驗證，不能承諾所有 PDF 都可無損分層。照片與插圖保留原始點陣格式，不宣稱變成向量或還原原始設計圖層。
