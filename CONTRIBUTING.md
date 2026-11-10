# 參與貢獻

感謝你願意協助 OpenLayer Studio。

## 原則

- **只用你有權公開的素材。** 測試、範例與 issue 附件請使用自製或合成圖檔，不要上傳客戶稿件、刊物或第三方作品。
- **不要提交模型權重、字型檔或 Adobe 產出的大型檔案**（.psd、.ai、.indd、.aep）。`.gitignore` 已排除 `models/`、`jobs/`、`reports/`。
- 遵守 [AGENTS.md](AGENTS.md) 與 [部件品質要求](docs/COMPONENT-QUALITY-REQUIREMENTS-zh-TW.md)：測試通過或 Adobe 能開啟，不等於視覺品質合格。

## 開發流程

```bash
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[test,fonts,graphics]'
pytest -q
```

送 PR 前請確認 `pytest -q` 通過，並在說明中寫出是否曾在實際 Adobe 應用程式驗證（版本、作業系統）。

## 授權

提交貢獻即表示你同意以 AGPL-3.0-or-later 授權你的貢獻內容。
