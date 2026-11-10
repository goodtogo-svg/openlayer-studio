"""Separate observed pixels from inferred occluded support, with explicit depth."""
import numpy as np


def partition_support(support, foreground):
    support = np.asarray(support, dtype=bool)
    foreground = np.asarray(foreground, dtype=bool)
    if support.ndim != 2 or support.shape != foreground.shape:
        raise ValueError('Support and foreground must be equally sized 2-D masks')
    return {'visible': support & ~foreground,
            'inferred': support & foreground, 'amodal': support.copy()}


def depth_order(nodes, behind_edges):
    nodes = list(nodes)
    if len(set(nodes)) != len(nodes):
        raise ValueError('Duplicate scene node')
    following = {n: [] for n in nodes}
    degree = dict.fromkeys(nodes, 0)
    for back, front in behind_edges:
        if back not in following or front not in following or back == front:
            raise ValueError('Invalid occlusion edge')
        if front not in following[back]:
            following[back].append(front)
            degree[front] += 1
    ready = [n for n in nodes if degree[n] == 0]
    result = []
    while ready:
        n = ready.pop(0)
        result.append(n)
        for child in following[n]:
            degree[child] -= 1
            if degree[child] == 0:
                ready.append(child)
    if len(result) != len(nodes):
        raise ValueError('Occlusion graph contains a cycle')
    return result
