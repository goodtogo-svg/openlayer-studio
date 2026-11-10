# 可追溯部件母版與 PSD 重建

**強制品質門檻：**先遵循 [部件最低品質要求 CQ-01..CQ-09](COMPONENT-QUALITY-REQUIREMENTS-zh-TW.md)，以使用者指定的完整建築素材為品質基準。編譯器輸出成功、分割集合正確或Alpha相同，不代表實體與材質已合格。先驗收母版，再登錄位置／遮罩及校準光效；失敗項須修正，不能以特效遮掩後宣告完成。

使用 `ali component-master PLAN.json --out NEW_JOB`，在原有分析、OCR、遮罩與文字樣式校準後，編譯已審閱部件。此流程不會自動認定所有AI候選都是最佳結果。

PLAN 包含來源 source、已驗證且有「03 Editable typography」群組的 base_psd、components 與 behind_edges。路徑相對PLAN目錄解析。每個部件含 id、support_mask（完整輪廓）、observed_mask（原圖可見區）、inferred_image（補全候選）、human_approved:true 與 provenance。所有素材使用來源全畫布尺寸；遮罩為二值輪廓。具體格式可參考 tests/test_component_master.py 內的合成 plan。

每個部件產出 OBSERVED、INFERRED 與 COMPLETE 三份RGBA素材，完整輪廓內全部覆蓋；原始可見像素直接取來源，不經生成。被遮擋的部分只取補全候選。缺審閱、缺來源、尺寸錯誤、輪廓外可見像素或前後循環會拒絕編譯。未實作物理軟透明度分離。

build-photoshop.jsx 在基底PSD的副本中保留原生文字及fx，隱藏舊素材群組，依遮擋順序新增部件群組，內含獨立嵌入智慧物件。COMPLETE素材供獨立重用，原生PSD則保留OBSERVED／INFERRED分離。腳本不自動執行，也不覆寫原PSD。輸出狀態保持 script_generated_not_executed；需由Photoshop實際存檔、重新開啟後另存驗收證據，不能只靠檔名宣告成功。

文字樣式沿用已驗證的原生文字層及 text-style-parameters.json；使用既有 generate-style-script.py 可由參數重製fx。樣式估計不是原稿參數；候選字型不是原字型身分證明。

品質交付依序檢查：原始可見像素一致、完整輪廓無透明洞、獨立素材位置、隱藏前景後的接縫、文字內容與fx、原生重新開啟。視覺品質仍需人審；不以無洞代替細節與幾何正確性。生成背景是新的畫面候選，不保證逐像素對齊原稿，嵌入前須先驗收。

## v2：乾淨母版與外觀參數分離

舊的OBSERVED是原圖像素，可能已烘焙陰影／光暈，不等於乾淨原始素材。每個component可新增appearance：

```json
{
  "clean_image": "key-clean-estimated.png",
  "support_mask": "key-physical-support.png",
  "status": "estimated_clean",
  "human_approved": true,
  "provenance": "去光效候選的來源、方法與人工校準記錄",
  "effects": {
    "outer_glow": {"enabled": true, "rgb": [255,150,30], "opacity": 55, "size_px": 18},
    "drop_shadow": {"enabled": true, "rgb": [10,10,40], "opacity": 45, "size_px": 12, "angle": 125, "distance_px": 20}
  }
}
```

support_mask需是完整實體輪廓，不包含外光暈。clean_image需覆蓋全部實體，不能留下前景遮擋造成的洞；這仍需人工審閱。母版輸出CLEAN-BASE.png和appearance.json；PSD只在乾淨智慧物件上套原生fx。原有烘焙外觀移入隱藏REFERENCE子群組，不再與新fx同時顯示。

預設status只能宣稱estimated_clean；若確有使用者提供的原始素材，使用provided_original並附original_evidence。程式驗證欄位，不能自行證明原始身分或圖像已真正去除所有效果。未知效果與無效參數會失敗，不會默默略過。

目前原生轉譯支援外光暈（Screen）及投影（Multiply），包含RGB、不透明度、尺寸、投影角度及距離。關閉fx應看到乾淨母版；原始參考維持隱藏。未提供appearance的舊component相容保留，但manifest明確標示clean_base_available=false，不宣稱已去特效。複雜金屬反射、內發光、曲線與環境色污染等尚未自動分離或擬合。

實測使用合成部件驗證乾淨像素、原生fx保存／讀回／關閉；不是這張海報所有主體完成去特效的證據。真實部件下一驗收需在黑、白、中灰底檢查殘色，再檢查套回效果與原圖的差異。

## 原圖實跑補充（2026-11-07）

實跑時，clean候選可由影像生成工具依原圖重建，完整提示與生成來源有記錄；不以原圖裁切冒充去特效。由全圖ROI做等比縮放及位置登錄，不代表幾何逐像素相同。soft physical alpha現可保留，不再強制轉二值。

可使用review_status="agent_reviewed"及review_notes記錄代理視覺審查，human_approved保持false，避免把代理驗收冒充使用者核准；舊human_approved:true仍相容。這不會免除最終使用者視覺驗收。真實效果參數目前為人工校準估計，並非自動反推原始濾鏡。只還原已支援的光暈和投影，其他照明、光帶、粒子未參數化。

原生腳本目前在finally恢復執行前rulerUnits及displayDialogs，避免留下使用者偏好變更。

## 高品質替換母版：保留高解析智慧物件

appearance.native_source_rgb可指定獨立高解析RGB來源，必須搭配native_mask:true、已登錄的physical alpha及native_transform（scale／tx／ty）。編譯器嵌入高解析來源，在Photoshop等比縮放／平移後才套全畫布遮罩，不先把智慧物件材質降成版面尺寸。登錄版RGBA仍供分析與中間素材使用，與嵌入高解析來源分開記錄。

ali.asset_registration可由最高點、落地線與門洞中心計算等比登錄，並從透明種子辨識封閉門洞；若種子區連到外部背景會失敗，不會把外部背景當作開口。等比登錄不保證新設計的每個點都與原稿一樣，必須保存錨點與視覺驗收。

更換母版後重新取得門洞／門框遮罩，重畫踏階光線，保留母版本身的倒角和材質，不能疊加舊平面門框覆蓋它。新母版的登錄與CQ審查結果應記錄在該工作的 reports/。
