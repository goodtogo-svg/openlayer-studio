# NOTICE

OpenLayer Studio
Copyright (C) 2026 樂易思維整合｜King Jam AI 及 OpenLayer Studio 貢獻者

本程式為自由軟體，依 GNU Affero General Public License 第 3 版或（由你選擇）任何更新版本授權，見 [LICENSE](LICENSE)。不提供任何擔保。

OpenLayer Studio 與 Adobe Inc. 無關聯，也未經其認可或贊助。Adobe、Photoshop、Illustrator、InDesign、After Effects 為 Adobe Inc. 在美國及／或其他國家的註冊商標或商標。

## 第三方元件

下列元件**不隨本專案原始碼散布**，由使用者以 pip 或 `ali models fetch` 從官方來源取得。授權摘要僅供參考，以各上游原文為準。

### 核心 Python 依賴

| 元件 | 授權 | 備註 |
|---|---|---|
| PyMuPDF | AGPL-3.0（或 Artifex 商業授權） | 本專案採 AGPL 的原因之一 |
| Pillow | MIT-CMU（HPND） | |
| psd-tools | MIT | |
| vtracer | MIT | |
| fontTools（選配） | MIT | |
| wordfreq（選配，補字字典） | Apache-2.0 | 詞頻資料含 CC BY-SA 4.0 來源 |
| pikepdf（選配） | MPL-2.0 | |
| pytest（測試） | MIT | |

### 模型引擎（各自獨立環境，`scripts/setup-v04.py` 安裝）

| 元件 | 程式授權 | 權重 | 商用注意 |
|---|---|---|---|
| SAM 2 / SAM 2.1 | Apache-2.0 | Apache-2.0 | — |
| PyMatting | MIT | 無 | — |
| PaddleOCR / PaddlePaddle / PaddleX | Apache-2.0 | PP-OCRv5，Apache-2.0 | — |
| Real-ESRGAN / BasicSR | BSD-3-Clause / Apache-2.0 | 隨 repo 發行 | 訓練資料有學術條款，請自行評估 |
| LaMa / IOPaint | Apache-2.0 | big-lama | **以 Places365 訓練（非商業研究條款），建議僅作研究／評估** |
| PyTorch / torchvision | BSD-3-Clause | — | — |
| OpenCV | Apache-2.0 | — | — |

### 字型（選配，不隨專案散布）

Inter、Noto Sans TC／SC／KR：SIL Open Font License 1.1。可隨軟體使用，但不得單獨販售字型本身；使用時須保留各字型的 OFL.txt。

字型比對只輸出候選名稱，不判定授權。使用者交付作品時須自行確認所用字型的授權。
