from pathlib import Path
import numpy as np
from PIL import Image

def load_image(path,max_pixels=16_000_000):
 with Image.open(path) as im:
  if im.width*im.height>max_pixels:raise ValueError('Engine image exceeds 16 MP limit; crop first')
  im.load();return im.convert('RGBA')

def torch_backend(requested='auto'):
 import torch
 if requested not in ('auto','mps','cpu'):raise ValueError('backend must be auto, mps or cpu')
 has=bool(torch.backends.mps.is_available())
 if requested=='mps' and not has:raise RuntimeError('MPS requested but unavailable')
 return 'mps' if has and requested!='cpu' else 'cpu'

def checkpoint(name):
 from ali.runtime import model_status,REGISTRY
 status=model_status(name)
 if not status['ready']:raise RuntimeError(f'Model {name}: {status["reason"]}; run ali models fetch {name}')
 return status['path'],REGISTRY[name]
