"""Bounded local LaMa inference. Original pixels outside the mask are immutable."""
from pathlib import Path
import numpy as np
from PIL import Image
from ali.runtime import atomic_json
from .common import checkpoint, load_image


def infer(req):
    import cv2
    import torch
    opts = req['options']
    model_path, _ = checkpoint(opts.get('model', 'inpaint-lama'))
    if opts.get('backend', 'cpu') != 'cpu':
        raise ValueError('This pinned LaMa adapter supports CPU only')
    image = load_image(req['source']).convert('RGB')
    mask_path = Path(opts['mask'])
    with Image.open(mask_path) as m:
        if m.size != image.size:
            raise ValueError('Mask dimensions differ from input')
        mask = np.asarray(m.convert('L')) > 0
    if not mask.any():
        raise ValueError('Inpainting mask is empty')
    limit = int(opts.get('max_side', 1280))
    if not 256 <= limit <= 1536:
        raise ValueError('max_side must be 256..1536 for the bounded CPU worker')
    original = np.asarray(image)
    h, w = mask.shape
    scale = min(1, limit / max(h, w))
    width, height = max(1, round(w * scale)), max(1, round(h * scale))
    small = cv2.resize(original, (width, height), interpolation=cv2.INTER_AREA)
    small_mask = cv2.resize(mask.astype('float32'), (width, height),
                            interpolation=cv2.INTER_AREA) > 0
    ph, pw = (-height) % 8, (-width) % 8
    small = np.pad(small, ((0, ph), (0, pw), (0, 0)), mode='symmetric')
    small_mask = np.pad(small_mask, ((0, ph), (0, pw)), mode='symmetric')
    torch.set_num_threads(4)
    model = torch.jit.load(model_path, map_location='cpu').eval()
    tensor = torch.from_numpy(small.transpose(2, 0, 1).copy()).float()[None] / 255
    matte = torch.from_numpy(small_mask.astype('float32'))[None, None]
    with torch.inference_mode():
        prediction = model(tensor, matte)[0, :, :height, :width]
    prediction = prediction.permute(1, 2, 0).cpu().numpy()
    prediction = np.clip(prediction * 255, 0, 255).astype('uint8')
    prediction = cv2.resize(prediction, (w, h), interpolation=cv2.INTER_CUBIC)
    result = np.where(mask[:, :, None], prediction, original)
    target = Path(req['out']) / 'inpainted-INFERRED.png'
    with Image.open(req['source']) as src:
        profile = src.info.get('icc_profile')
    Image.fromarray(result).save(target, **({'icc_profile': profile} if profile else {}))
    metadata = {'backend': 'cpu', 'model': opts.get('model', 'inpaint-lama'),
                'image': str(target), 'is_original': False,
                'provenance': 'LaMa model inference, not original concealed data',
                'quality': {'inference_size': [width, height],
                            'masked_pixels': int(mask.sum()),
                            'unmasked_changed_pixels': int(np.any(result != original, axis=2)[~mask].sum())}}
    atomic_json(Path(req['out']) / 'provenance.json', metadata)
    return metadata
