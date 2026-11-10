# PSD 第二階段：樣式、補全與遮擋

## 樣式參數

`ali text-styles SOURCE --regions TEXT-PLAN.json --out NEW-REPORT.json` 以可讀文字區估計填色、垂直漸層及外光暈。計算使用背景中位數、亮色核心與環狀能量；不是原PSD metadata。字型與字距來自已審閱重排計畫，不宣稱由影像可靠識別。

實測另建立校準參數JSON、可重製的Photoshop套用腳本，並讀回ActionDescriptor輸出原生實際數值。主標細斜角為手動校準的重建選擇；平面圖無法唯一判斷斜角／描邊／手繪高光，須保留不確定性。改JSON後使用交付的generate-style-script.py產生新的JSX，或直接在Photoshop fx面板調整。

## 本機補全

```sh
.venv/bin/python scripts/setup-v04.py inpaint
.venv/bin/ali models fetch inpaint-lama --accept-research-license  # 僅研究／評估用途，見 NOTICE.md
.venv/bin/ali engine inpaint INPUT.png --out NEW_JOB --options '{"mask":"/absolute/mask.png","backend":"cpu","max_side":1280}'
```

獨立Python3.11環境，Torch2.5.1／NumPy1.26.4／Pillow11.3.0／OpenCV4.11.0.86。TorchScript LaMa使用CPU、四執行緒、1536px上限，先按8的倍數補邊。補全後原樣放回遮罩外像素；缺模型、空遮罩、尺寸錯誤與推論失敗均非成功。任務狀態與mask的SHA256支援可驗證恢復。

大物件需包含光暈及真實鏤空的背景部分，不能只移除物件實體輪廓。初次LaMa候選仍有殘影，本案例擴大遮罩後再推論；雲層細紋另以低幅度來源紋理延續。未宣稱原稿背景、超解析或有精確真值的品質分數。

## 完整主體與遮擋

`ali.occlusion.partition_support`分開完整輪廓S、可見V、推估I：V與I互斥，V∪I=S。前景可以覆蓋後方主體的完整輪廓，不能直接從S扣掉前景後就當作完整物件。真正的鑰匙孔原本不在S，不能填成實體。

本例建築群組包含原始可見像素及標示INFERRED的隱藏表面。初次窄遮罩LaMa與單純平面材質延續候選有殘影或接縫，未採用為主結果；最終採擴大遮罩、1536px的LaMa補全，加低幅度來源紋理。85,709個推估像素中85,659個由模型覆蓋，50個邊緣像素使用平面延續後備。推估表面仍有色階接縫及幾何不確定性，不能視為原始被遮擋內容。禮物及其周圍原始光效一起分組；前景雲另存，記錄背景→建築→雲／鑰匙／人物／禮物的DAG。這不是各材質的物理透明度分解。

驗收必須重新開啟PSD、隱藏禮物、單獨顯示建築、檢查完整輪廓內無透明洞且輪廓外透明。也要測試整組移動與JSON參數實際改變原生效果。不能只用全圖合成好看或原圖參考層遮住缺口作證。
