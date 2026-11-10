# 原圖外觀的參數化重建

**前置要求：**部件母版必須先通過 [最低品質要求](COMPONENT-QUALITY-REQUIREMENTS-zh-TW.md)。光效／濾鏡／環境遮罩不能用來掩蓋裂縫、漏底、錯誤透視、平面化或不完整的實體。更換母版後須重新登錄門洞、階梯及落地錨點，不得直接沿用不合適的舊效果座標。

`ali scene-effects PLAN.json --out NEW_JOB` 將效果參數轉為獨立透明素材與Photoshop重建腳本。這是手動校準後可重製的外觀，不是原始濾鏡參數的自動還原。

PLAN有size、base_psd及layers。每層包含id、owner、kind、rgb、opacity、blur_px和blend。owner為portal／key／person／gifts。支援Screen、Normal、Linear Dodge；本案實測使用Screen。id不可重複。輸入需符合尺寸、數值範圍與類型限制。

支援圖元：

- arch：box與gradient_rgb（上／下兩端顏色），建立圓頂門面漸層。
- ellipse：box，建立柔和放射亮度場。
- curves：segments（三次貝茲曲線的四個XY控制點）、width_px；可用窄核心＋寬模糊層組合光帶。
- stars：stars中的x、y、radius、rotation，建立四角星芒；另加45度星可得八角變化。
- particles：box、count、radius_px、seed，固定種子可重製相同分布。
- rim：alpha_mask、width_px，從母版透明輪廓推導內側亮邊，不改母版像素。

位置、顏色、形狀及模糊儲存在JSON，修改後重新編譯素材；它們不是宣稱可直接在Photoshop面板改數值的原生向量或Smart Filter。PSD中每層為獨立嵌入智慧物件，混合模式、不透明度與可見性可直接調整。光帶控制點、粒子種子及blur_px需由JSON重製。原有clean母版的原生光暈／投影仍可在fx面板調整。

腳本開啟基底PSD後建立副本，在對應物件內建立PARAMETRIC FX群組。重製時只在新副本替換同名效果群組，避免累加舊效果；不移除母版或來源參考。圖層前後順序沿用部件順序，物件內依layers順序由後向前疊加。輸出新路徑且拒絕覆寫；執行後恢復Photoshop尺規與對話框設定。

必須在Photoshop存檔、重新開啟、確認層數與混合模式，匯出效果開關比較，檢查原母版資料未改，再宣告原生輸出完成。manifest生成狀態不代表原生驗證通過。

本案有24層效果，門面、旋光、階梯光帶、星芒、粒子及輪廓亮邊各自可關閉。背景既有星空光帶仍屬背景圖像；沒有把全部背景照明分解。完整照明反射、光影物理分解、原字型及幾何精確還原仍未完成。

## 位置與畫面邏輯校正

`ali.layout_geometry`提供明確的分段單調座標變換；部件與光效錨點共用同一變換，拒絕折返／不完整範圍，RGBA插值使用預乘Alpha避免黑邊。此為人工校準幾何，不是自動判斷正確構圖。

新增clip_mask／exclude_mask與clip_stage（pre_blur或post_blur）。核心光線、粒子與門面可在模糊後嚴格裁切，外暈可先裁切再模糊以保留合理溢光。ellipse可用angle_deg旋轉地面投光；rim可指定light_position及ambient_floor，依Alpha法線近似朝向光源的亮邊。這不是物理渲染或從原圖辨識光源真值。

blend新增multiply。placement=behind會建立PARAMETRIC FX BACK群組並放在乾淨母版下方，適用接觸陰影／地面投光；front為預設。重建時兩種群組一起替換。arch_rise_px可獨立控制拱頂高度，避免門洞變寬後仍硬套正半圓。

實際案例中：建築升高、門洞左移放大、人物右移、禮物寬度校正；七條踏階邊緣人工校準；陰影在實體後方；門內光及粒子裁切；亮邊朝門口。跨鑰匙與門口的光帶由兩者錨點校準，移動部件後仍需重製，沒有宣稱Photoshop內有即時約束系統。

## 原圖姿態／幾何優先，濾鏡與遮罩原生化

V3改用原圖人物像素與審閱遮罩，不再以生成姿態做平移／拉伸。保守高光壓縮、圍巾白平衡只是去光照估計，並不宣稱從單張圖得到真實無光照材質。門、牆面與階梯依原圖人工描定，材質使用中性候選細紋及原圖可見藍色材質高頻細節，遮擋後表面為估計。ali.source_geometry保存貝茲門框、材質投影及原姿態保留的運算。

appearance.native_mask=true輸出完整RGB智慧物件與獨立PHYSICAL_ALPHA，並在Photoshop建立可編輯原生圖層遮罩，RGBA母版仍另行輸出。appearance.grade_curves可指定composite／red／green／blue曲線（0..255），建立剪裁至實體的原生Curves調整層。environment_mask則作用於部件群組，與完整實體遮罩分開；關閉環境遮罩可見完整母版，不會把雲霧／前景缺口烘焙進實體。

scene-effects.native_constraints=true可將post_blur裁切／排除約束放到原生圖層遮罩，效果素材本身保留未裁切內容；pre_blur效果則保留先裁切再模糊的光學溢光。每層native_constraint=false可保留固有Alpha，例如門框材質斜角需要由固有形狀計算。native_bevel建立原生innerBevel樣式，數值包含尺寸、柔化、深度、角度、高光／陰影色與不透明度；不是以亮邊PNG冒充原生斜角。

前後效果群組保留時，BACK群組明確放在CLEAN BASE智慧物件之後，不能假設第一個artLayer永遠是母版（現在可能是Curves）。本案移除造成硬缺口的舊來源雲霧context，改以獨立環境遮罩控制，不把遮擋造成的破洞當成建築形狀。

## 已通過部件之後：只更新濾鏡／光暈／特效

部件及位置經使用者確認後，下一輪不得反覆提取、重畫或重新登錄。以通過的PSD為基底，固定母版內容、智慧物件變換、實體遮罩及環境遮罩，僅更新外觀控制。

scene-effects計畫可提供component_grading（各owner的原生Curves）與component_fx（原生outer_glow／drop_shadow完整設定）。編譯器只在新副本替換控制中的FILTER層與效果群組，不呼叫部件resize／translate，也不套新的部件遮罩。component_fx全部停用時用Photoshop原生效果可見性關閉，再啟用時恢復顯示；不是刪除智慧物件或只在JSON寫enabled=false。

本案FX V5基於已通過V4部件。母版嵌入SHA256、變換矩陣、物理Alpha及環境Alpha均須比對一致；再比較雙方關閉部件調色／fx後的畫面，避免只靠「程式沒報錯」判定沒有碰部件。效果位置與模糊仍由JSON重製；PSD內曲線、圖層fx、可見性、混合模式及不透明度可直接調整。

## 光效參考匹配：擴散強度與雲霧受光

每層intensity為模糊後的Alpha強度增益（0..16，預設1），與原生opacity分開；用來補償細光線模糊後峰值衰減，不是物理光子追蹤。post_blur約束在增益後套用，不能因增益穿出嚴格裁切遮罩。

owner=environment在背景上方、既有部件下方建立ENVIRONMENT FX；不受portal群組環境遮罩限制，也不改背景像素／物理Alpha。雲霧可用新的效果接收遮罩，依亮度及高度篩選受光範圍，與使用者已通過的部件遮罩分離。

blend=color使用Photoshop原生BlendMode.COLORBLEND；分離染色與加亮。可用Color層保留材質明暗，再以Screen層增加亮度，避免只有白霧而缺少暖金／粉紫反射。此列舉值已在實際Photoshop驗證；Python生成JSX通過不能代替原生API驗證。

scene-effects完成或失敗後會關閉自己建立的暫存副本，保留新儲存PSD及原本開啟的來源文件。原生檔仍需存檔重開及視覺檢查。V6記錄各次候選，最終選擇降低過曝、保留星芒及材質的校準版本，不以第一版自動視為通過。
