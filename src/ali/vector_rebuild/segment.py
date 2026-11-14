"""Tiled SAM 2.1 at full resolution -> merged masks -> non-overlapping partition.

The stock segmentation worker samples an 8x8 point grid on the whole image, which misses most parts of a
wide banner or a tall poster. Here square-ish tiles are sampled densely, masks cut by a tile edge are
joined across the overlap, near-duplicates are dropped and the result is painted into a label map
(large regions first, smaller on top), so every pixel belongs to exactly one region.
"""
import numpy as np


def tiled_masks(src, model_name='sam2-small', tile=520, overlap=100, points_per_side=24, log=print):
    import torch
    from ali.runtime import REGISTRY, model_status
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
    from sam2.build_sam import build_sam2
    torch.set_num_threads(max(1, torch.get_num_threads()))
    status = model_status(model_name)
    if not status['ready']:
        raise RuntimeError('Model %s not ready: %s' % (model_name, status['reason']))
    model = build_sam2(REGISTRY[model_name]['config'], status['path'], device='cpu', apply_postprocessing=False)
    gen = SAM2AutomaticMaskGenerator(model, points_per_side=points_per_side, points_per_batch=64, pred_iou_thresh=0.72,
                                     stability_score_thresh=0.85, min_mask_region_area=40)
    H, W = src.shape[:2]
    tw, th = min(tile, W), min(tile, H)
    step_x, step_y = max(1, tw - overlap), max(1, th - overlap)
    xs = sorted(set(list(range(0, max(1, W - tw), step_x)) + [W - tw]))
    ys = sorted(set(list(range(0, max(1, H - th), step_y)) + [H - th]))
    masks = []
    for y0 in ys:
        for x0 in xs:
            for m in gen.generate(src[y0:y0 + th, x0:x0 + tw]):
                masks.append({'x0': x0, 'y0': y0, 'w': tw, 'h': th, 'seg': m['segmentation'],
                              'score': float(m['predicted_iou']) * float(m['stability_score'])})
            log('tile %d,%d -> %d masks' % (x0, y0, len(masks)))
    return masks


def merge_masks(masks, H, W, min_area=150):
    full = []
    for m in masks:
        f = np.zeros((H, W), bool)
        f[m['y0']:m['y0'] + m['h'], m['x0']:m['x0'] + m['w']] = m['seg']
        full.append(f)
    n = len(masks)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def cut(i):
        m, f = masks[i], full[i]
        x0, y0, x1, y1 = m['x0'], m['y0'], m['x0'] + m['w'] - 1, m['y0'] + m['h'] - 1
        return ((x0 > 0 and f[:, x0].any()) or (x1 < W - 1 and f[:, x1].any()) or
                (y0 > 0 and f[y0, :].any()) or (y1 < H - 1 and f[y1, :].any()))
    cuts = [cut(i) for i in range(n)]
    for a in range(n):
        if not cuts[a]:
            continue
        A = masks[a]
        for b in range(n):
            if b == a or (masks[b]['x0'], masks[b]['y0']) == (A['x0'], A['y0']):
                continue
            B = masks[b]
            lx, hx = max(A['x0'], B['x0']), min(A['x0'] + A['w'], B['x0'] + B['w'])
            ly, hy = max(A['y0'], B['y0']), min(A['y0'] + A['h'], B['y0'] + B['h'])
            if hx <= lx or hy <= ly:
                continue
            sa, sb = full[a][ly:hy, lx:hx], full[b][ly:hy, lx:hx]
            u = (sa | sb).sum()
            if u >= 30 and (sa & sb).sum() / u > 0.75:
                parent[find(a)] = find(b)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    merged = []
    for g in groups.values():
        f = np.zeros((H, W), bool)
        for i in g:
            f |= full[i]
        merged.append((f, max(masks[i]['score'] for i in g)))
    merged.sort(key=lambda t: -t[1])
    kept = []
    for f, s in merged:
        if f.sum() < min_area:
            continue
        if any((f & k).sum() / (f | k).sum() > 0.8 for k in kept):
            continue
        kept.append(f)
    return kept


def partition(masks, H, W, whole_object_area=0.04, min_piece=250):
    """Label map: larger masks first, smaller painted on top; children of a small 'whole object' are dropped
    so an object stays one piece; tiny exposed fragments join the region around them."""
    import cv2
    area = np.array([m.sum() for m in masks])
    order = list(np.argsort(-area))
    drop = set()
    for i in order:
        if area[i] > whole_object_area * H * W:
            continue
        for j in order:
            if j != i and j not in drop and area[j] < area[i] and (masks[j] & masks[i]).sum() > 0.9 * area[j]:
                drop.add(j)
    idx = [i for i in order if i not in drop]
    label = np.full((H, W), -1, int)
    for k, i in enumerate(idx):
        label[masks[i]] = k
    for k in range(len(idx)):
        reg = label == k
        if 0 < reg.sum() < min_piece:
            ring = cv2.dilate(reg.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & ~reg
            vals = label[ring]
            vals = vals[vals != k]
            label[reg] = np.bincount(vals + 1).argmax() - 1 if len(vals) else -1
    # unassigned islands that are not tiny become their own regions
    loose = (label < 0).astype(np.uint8)
    count, cc, stats, _ = cv2.connectedComponentsWithStats(loose, 8)
    nxt = label.max() + 1
    for c in range(1, count):
        if stats[c, 4] >= 400 and stats[c, 4] < 0.5 * H * W:
            label[cc == c] = nxt
            nxt += 1
    return label
