"""Run PaddleOCR on several images in the OCR environment: python ocr_batch.py out.json img1.png img2.png ...

Uses the same local PP-OCRv5 weights as the `ocr` engine (models/paddlex/official_models)."""
import json
import sys
from pathlib import Path


def main():
    out, images = sys.argv[1], sys.argv[2:]
    import numpy as np
    from PIL import Image
    from paddleocr import PaddleOCR
    root = Path(__file__).resolve().parents[3]
    cache = root / 'models/paddlex/official_models'
    kwargs = {}
    for prefix, name in (('text_detection', 'PP-OCRv5_server_det'), ('text_recognition', 'PP-OCRv5_server_rec')):
        kwargs[prefix + '_model_name'] = name
        if (cache / name / 'inference.pdiparams').is_file():
            kwargs[prefix + '_model_dir'] = str(cache / name)
    engine = PaddleOCR(lang='chinese_cht', use_doc_orientation_classify=False, use_doc_unwarping=False,
                       use_textline_orientation=False, device='cpu', **kwargs)
    results = []
    for path in images:
        recs = []
        for pred in engine.predict(np.array(Image.open(path).convert('RGB'))):
            data = pred.json
            data = json.loads(data) if isinstance(data, str) else data
            data = data.get('res', data)
            for text, score, poly in zip(data.get('rec_texts', []), data.get('rec_scores', []), data.get('rec_polys', data.get('dt_polys', []))):
                recs.append({'text': text, 'confidence': float(score), 'polygon': np.asarray(poly).tolist()})
        results.append(recs)
    Path(out).write_text(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
