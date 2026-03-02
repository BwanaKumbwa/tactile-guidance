import sys
from pathlib import Path
import os

# Setup paths
current_dir = Path(__file__).resolve()
project_root = current_dir.parents[1]

if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

os.chdir(project_root)

sys.path.append(os.path.join(project_root, 'yolov5'))
sys.path.append(os.path.join(project_root, 'strongsort'))
sys.path.append(os.path.join(project_root, 'midas'))
sys.path.append(os.path.join(project_root, 'unidepth'))

import cv2
import numpy as np
from threading import Event

# YOLOv5 Imports
try:
    from utils.augmentations import letterbox
except ImportError:
    # Fallback if your project structure treats yolov5 as a package
    from yolov5.utils.augmentations import letterbox

class AndroidSource:
    """
    Mimics YOLOv5 LoadStreams.
    Yields: 4D numpy array (1, 3, H, W)
    """
    def __init__(self, frame_queue, img_size=640, stride=32, auto=True):
        self.frame_queue = frame_queue
        self.img_size = img_size
        self.stride = stride
        self.auto = auto
        self.mode = 'stream'
        self.sources = ['Android_Stream'] 
        self.stop_event = Event()
        self.count = 0

    def __iter__(self):
        self.count = -1
        return self
    
    def __next__(self):
        self.count += 1
        if self.stop_event.is_set():
            raise StopIteration

        # Unpack the tuple
        item = self.frame_queue.get()
        if item is None: raise StopIteration
        
        im0, depth_map = item
        
        # Save it to the dataset object so controller.py can access it
        self.current_depth = depth_map 

        # Resize + pad to a square TARGET_SIZE×TARGET_SIZE
        TARGET = self.img_size
        h, w = im0.shape[:2]

        scale = TARGET / max(h, w)
        new_w, new_h = int(w * scale), int(h * scale)

        resized = cv2.resize(im0, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        pad_w = TARGET - new_w
        pad_h = TARGET - new_h
        top, bottom = pad_h // 2, pad_h - pad_h // 2
        left, right = pad_w // 2, pad_w - pad_w // 2

        im = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                cv2.BORDER_CONSTANT, value=(114, 114, 114))

        # BGR → RGB, HWC → CHW, add batch dim
        im = letterbox(im0, self.img_size, stride=self.stride, auto=self.auto)[0]
        im = im.transpose((2, 0, 1))[::-1]  
        im = np.ascontiguousarray(im)
        im = im[None] 

        return self.sources, im, [im0], None, ''

    def __len__(self):
        return 0 