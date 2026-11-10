# v0.4 模型、版本與授權

> **商用前必讀**：以下摘要不是法律意見。權重不隨本專案散布；使用者執行下載指令即與各上游授權直接成立關係。
>
> - **LaMa（big-lama.pt）僅建議研究／評估用途。** 程式為 Apache-2.0，但權重以 Places365 訓練，該資料集條款為非商業研究。商業交付請改用自行確認授權的補全方式（例如在自己授權的 Photoshop 內補全）。
> - **Real-ESRGAN 權重**隨 BSD-3 repo 發行，但訓練資料（DIV2K 等）有學術使用條款，商用請自行評估。
> - SR 環境安裝時不含 gfpgan／facexlib（人臉修復，本專案不使用，且含非商用元件）。

所有推論在本機執行，不使用付費 API。下載只取得官方開源程式、權重及附授權的字型；原始設計檔不送出網路。模型授權與商用適用性仍需依實際使用範圍核對。

| 引擎 | 本次直接版本／模型 | 來源及授權 |
|---|---|---|
| SAM | Torch 2.5.1、torchvision 0.20.1；SAM commit `2b90b9f5ceec907a1c18123530e92e794ad901a4`；SAM 2.1 Tiny/Small | [Meta 官方 repository](https://github.com/facebookresearch/sam2)，Apache-2.0；權重來自官方 092824 發行路徑 |
| Matting | PyMatting 1.1.14 | [PyMatting](https://github.com/pymatting/pymatting)，MIT；不是學習式 matting 模型 |
| OCR | PaddleOCR 3.2.0、PaddlePaddle 3.2.0、PaddleX 3.2.1 | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)、[PaddlePaddle](https://github.com/PaddlePaddle/Paddle)，Apache-2.0；[官方 OCR 操作文件](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html) |
| OCR detection | PP-OCRv5_server_det | PaddleX 官方模型；下載記錄於各 inference.log 與 models/ocr receipts |
| OCR recognition | PP-OCRv5_server_rec、en_PP-OCRv5_mobile_rec、korean_PP-OCRv5_mobile_rec | 官方模型；繁／簡／日使用 server_rec，en／ko 使用各自 mobile_rec |
| SR | Real-ESRGAN 0.3.0、BasicSR 1.4.2、Torch 2.5.1 | [Real-ESRGAN 官方](https://github.com/xinntao/Real-ESRGAN)，BSD-3-Clause；[BasicSR](https://github.com/XPixelGroup/BasicSR)，Apache-2.0 |
| SR checkpoints | RealESRGAN_x4plus、RealESRGAN_x4plus_anime_6B | 官方 GitHub releases v0.1.0、v0.2.2.4；模型 registry 保留來源。需留意上游訓練資料權利不等於程式碼授權 |
| Fonts | fontTools 4.68.0；Inter、Noto Sans TC/SC/KR | [Google Fonts 官方倉庫](https://github.com/google/fonts/tree/main/ofl)，各字型附 OFL.txt，SIL OFL-1.1；不散布商業字型 |

完整套件鎖定：`models/locks/*-resolved.txt`；直接需求：`models/locks/{segmentation,ocr,sr,fonts}.txt`。鎖定檔固定版本但不是所有 wheel 的 hash lock，也不是跨 OS 的 lock。這次實測目標為 macOS arm64、Python 3.11.18。

## 安裝

```bash
.venv/bin/python scripts/setup-v04.py segmentation
.venv/bin/python scripts/setup-v04.py ocr
.venv/bin/python scripts/setup-v04.py sr
.venv/bin/python scripts/setup-v04.py fonts
.venv/bin/ali models fetch sam2-tiny
.venv/bin/ali models fetch sam2-small
.venv/bin/ali models fetch sr-photo
.venv/bin/ali models fetch sr-illustration
```

setup 的 SAM2_BUILD_CUDA=0 僅作用於子程序，不寫入 shell profile。不需要 CUDA，M2 使用 MPS 或 CPU。BasicSR 的舊 functional_tensor 匯入以 worker 內明確 alias 對應 torchvision functional；沒有任意降級依賴，也沒有修改 site-packages。

OCR 首次透過 `ali engine ocr INPUT --out NEW_JOB --options '{"language":"zh-TW","download_models":true}'` 下載；後續省略 download_models。下載快取位於 models/paddlex，與一般使用者快取隔離。

字型候選 catalog：

```json
{"fonts":[{"path":"Inter.ttf","license":"OFL-1.1","license_file":"Inter-OFL.txt"}]}
```

路徑相對 catalog；font-index 輸出保存絕對路徑、授權及檔案 digest。catalog 是操作員明確聲明的授權證據，不是法律授權自動判定器。當前本機候選含 5 個由 Inter variable font 產生的靜態字重，及 Noto Sans TC；OFL 文件保留於 models/fonts。權重產生未更動上游來源字型。

## 完整性與隱私

checkpoint 第一次下載後計算 SHA256，後續載入前比對；目前沒有官方公佈 digest 的獨立核對或簽章驗證。models 及 env 以 .gitignore 排除，避免誤提交大型權重、快取與本機路徑。測試資料是合成幾何與明確字串；既有刊物只用於本機 PDF 原生文字保留測試，沒有拿來訓練模型。

## PSD 第二階段：LaMa 本機補全

新增 `inpaint-lama`（LaMa Big TorchScript / IOPaint export），程式來源 [LaMa](https://github.com/advimman/lama)、[IOPaint 模型介面](https://github.com/Sanster/IOPaint/blob/main/iopaint/model/lama.py)，專案程式為 Apache-2.0。權重使用該介面指定的維護者發行資產，已比對上游MD5 `e3aa4aaa15225a33ec84f9f4bc47e500`，並保存SHA256 `344c77bbcb158f17dd143070d1e789f38a66c04202311ae3a258ef66667a9ea9`。這不是上游簽章驗證，也不是訓練資料權利自動保證。

環境 `models/envs/inpaint`，直接鎖定 `models/locks/inpaint.txt`，完整版本 `inpaint-resolved.txt`。安裝時明確用專案Python3.11，setup指令也拒絕低於3.11。模型只下載到專案，不上傳設計檔、不使用付費API。輸出均標為推估；建築隱藏面採擴大遮罩後的1536px LaMa候選，50個邊緣像素採平面材質後備；初次窄遮罩與單純平面候選未採用為主結果。
