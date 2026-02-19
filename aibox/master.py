import sys
import os
from pathlib import Path

file = Path(__file__).resolve()
root = file.parents[0]
os.chdir(root)

sys.path.append('/yolov5')
sys.path.append('/strongsort')
sys.path.append('/midas')
sys.path.append('/unidepth')

import argparse
import json
import controller
import torch
import cv2
from bracelet import connect_belt, BraceletController


def run_experiment_logic(args, mcp_queue=None, shared_state=None):
    participant = args.participant
    condition = args.condition

    participant = args.participant
    condition = args.condition
    metric = (not args.relative) and torch.cuda.is_available()
    mock_navigate = args.mock_navigate
    save_video = args.save_video

    weights_obj = 'weights/yolov5s.pt'
    weights_hand = 'weights/hand_v5_optivist.pt'

    run_object_tracker = True if condition == 'multiple_objects' else False
    weights_tracker = 'weights/osnet_x0_25_market1501.pt'

    run_depth_estimator = True if condition == 'depth_navigation' else False
    weights_depth_estimator = (
        'v2-vits14' if metric else 'midas_v21_384'
    )

    available_sources = []
    for s in range(100):
        cap = cv2.VideoCapture(s)
        if cap.isOpened():
            available_sources.append(str(s))
            cap.release()
        else:
            break

    select_source_manually = False
    if select_source_manually:
        try:
            source = input(
                f'Available sources: {available_sources}. '
                'Please select the camera source: '
            )
            if source not in available_sources:
                raise ValueError
        except ValueError:
            print(
                f'Invalid source. Defaulting to first available '
                f'source ({available_sources[0]}).'
            )
            source = available_sources[0]
    else:
        try:
            source = available_sources[1]
        except Exception:
            source = available_sources[0]

    belt_controller = None

    target_objs = []
    output_path = 'results/' + f'{condition}/'

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    try:
        with open(
            'results/calibration/'
            f'calibration_participant_{participant}.json'
        ) as file:
            participant_vibration_intensities = json.load(file)
        print('Calibration intensities loaded succesfully.')
    except Exception:
        baseline_value = 30
        print(
            f'\nError while loading the calibration file. '
            f'Continuing with baseline intensity of {baseline_value} '
            'for each vibromotor.'
        )
        participant_vibration_intensities = {
            'bottom': baseline_value,
            'top': baseline_value,
            'left': baseline_value,
            'right': baseline_value,
        }

    print(f'\nLOADING CAMERA AND BRACELET')

    try:
        source = str(source)
        print('Camera connection successful')
    except Exception:
        print('Cannot access selected source. Aborting.')
        sys.exit()

    if not mock_navigate:
        connection_check, belt_controller = connect_belt()
        if connection_check:
            print('Bracelet connection successful.')
        else:
            print('Error connecting bracelet. Aborting.')
            sys.exit()

    try:
        bracelet_controller = BraceletController(
            vibration_intensities=participant_vibration_intensities,
            navigation_type=1,
        )
        task_controller = controller.TaskController(
            mcp_queue=mcp_queue,
            shared_state=shared_state,
            weights_obj=weights_obj,
            weights_hand=weights_hand,
            weights_tracker=weights_tracker,
            weights_depth_estimator=weights_depth_estimator,
            source=source,
            iou_thres=0.45,
            max_det=1000,
            device='',
            view_img=True,
            save_txt=False,
            imgsz=(640, 640),
            conf_thres=0.7,
            save_conf=False,
            save_crop=False,
            nosave=not save_video,
            classes_obj=[1, 39, 40, 41, 42, 45, 46, 47, 58, 74],
            classes_hand=[0, 1],
            agnostic_nms=False,
            augment=False,
            visualize=False,
            update=False,
            project=output_path,
            name='video/',
            exist_ok=False,
            line_thickness=2,
            hide_labels=False,
            hide_conf=False,
            half=False,
            dnn=False,
            vid_stride=1,
            manual_entry=False,
            run_object_tracker=run_object_tracker,
            run_depth_estimator=run_depth_estimator,
            mock_navigate=mock_navigate,
            belt_controller=belt_controller,
            tracker_max_age=60,
            tracker_n_init=5,
            target_objs=target_objs,
            output_data=[],
            output_path=output_path,
            condition=condition,
            participant=participant,
            participant_vibration_intensities=participant_vibration_intensities,
            bracelet_controller=bracelet_controller,
            metric=metric,
        )

        task_controller.run()

    except KeyboardInterrupt:
        controller.close_app(belt_controller)

    controller.close_app(belt_controller)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Argument parser for bracelet tasks."
    )
    parser.add_argument("-p", "--participant", type=int, required=True)
    parser.add_argument("-c", "--condition", type=str, required=True,
                        choices=['grasping', 'multiple_objects', 'depth_navigation'])
    parser.add_argument("--relative", action="store_true")
    parser.add_argument("--mock_navigate", action="store_true")
    parser.add_argument("--save_video", action="store_true")

    args = parser.parse_args()
    run_experiment_logic(args, mcp_queue=None, shared_state=None)