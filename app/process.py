from flask import request
import json
from app import *
import cv2
import numpy as np
from math import *

from camera import Camera, generate_video
from surface import Surface, Polyhedron, Space
from cut_image import *

SLICED_IMAGE_PATH = STATIC_PATH + '/img/sliced'

@app.route('/generate_video', methods=['POST'])
def process():
    data = json.loads(request.data)
    space = Space()
    world_dimension_data = data['world']
    space_dimension = (world_dimension_data['width'], world_dimension_data['height'], world_dimension_data['depth'])
    inner_rect_data = data['planeRect']
    topleft = (inner_rect_data['x'], inner_rect_data['y'])
    bottomright = (inner_rect_data['x'] + inner_rect_data['width'], inner_rect_data['y'] + inner_rect_data['height'])
    inner_box = (topleft, bottomright)
    vanishing_point = (data['vanishingPoint']['x'], data['vanishingPoint']['y'])
    image_name = data['image']

    surfaces = cut_image(image_name, space_dimension, inner_box, vanishing_point)
    space.add_model(Polyhedron(surfaces))

    camera_width = 970
    camera_height = 400
    camera_depth = world_dimension_data['depth']
    BEZIER_PATH_ORDER = 3
    camera = Camera(camera_depth/2, width=camera_width, height=camera_height)
    camera_path, camera_angles = generate_bezier_path_and_orientations(data['camera_path'], BEZIER_PATH_ORDER)
    filtered_camera_path, filtered_camera_angles = [], []
    for i in range(len(camera_path)):
        if int(camera_angles[i]) != 0:
            filtered_camera_path.append(camera_path[i])
            filtered_camera_angles.append(camera_angles[i])
    
    smooth_camera_path, smooth_camera_angles = smoothen_camera(filtered_camera_path, filtered_camera_angles)
    
    frames = []
    num_of_frames = len(smooth_camera_path)
    for index, (position, orientation) in enumerate(zip(smooth_camera_path, smooth_camera_angles)):
        print 'Generating frame:', index + 1, '/', num_of_frames
        camera.position = position
        camera.orientation = orientation
        frame = camera.project_space(space)
        frames.append(frame)

    file_name = data['file_name']
    file_path = generate_video(camera.width, camera.height, frames, file_name)

    return json.dumps({ 'status': 'success', 
                        'video': {'name': file_name, 'width': camera_width, 'height': camera_height, 'src': file_path}
                    })

NUM_LINE_SEGMENTS = 256

def generate_bezier_path_and_orientations(points_list, order):
    # Camera's horizontal axis is world's x-axis
    # Camera's vertical axis is the opposite of the world's z-axis
    # Camera's optical axis is the world's y-axis
    # forward: [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    num_bezier_sets = (len(points_list) - 1) / order
    prev_p2 = {}
    path_points = []
    path_angles = []
    for i in range(num_bezier_sets):
        p0 = points_list[i * order]
        if i != 0:
            # Modify the second point to make it C1 continuous
            diff_x = p0['x'] - prev_p2['x']
            diff_y = p0['y'] - prev_p2['y']
            p1 = {}
            p1['x'] = p0['x'] + diff_x
            p1['y'] = p0['y'] + diff_y
            p1['z'] = points_list[i * order + 1]['z']
        else:
            p1 = points_list[i * order + 1]
        p2 = points_list[i * order + 2]
        p3 = points_list[i * order + 3]
        segment_chunk_length = NUM_LINE_SEGMENTS / 3
        for j in range(NUM_LINE_SEGMENTS):
            # Generate path points between control points
            t = float(j) / NUM_LINE_SEGMENTS
            x = ((1-t)**3)*p0['x'] + 3*((1-t)**2)*t*p1['x'] + 3*(1-t)*(t**2)*p2['x'] + (t**3)*p3['x']
            y = ((1-t)**3)*p0['y'] + 3*((1-t)**2)*t*p1['y'] + 3*(1-t)*(t**2)*p2['y'] + (t**3)*p3['y']
            if 0 <= j < segment_chunk_length:
                z = (p1['z']-p0['z']) * (float(j%segment_chunk_length)/segment_chunk_length) + p0['z']
            elif segment_chunk_length <= j < 2*segment_chunk_length:
                z = (p2['z']-p1['z']) * (float(j%segment_chunk_length)/segment_chunk_length) + p1['z']
            elif 2*segment_chunk_length <= j:
                z = (p3['z']-p2['z']) * (float(j%segment_chunk_length)/segment_chunk_length) + p2['z']
            path_points.append((x, y, z))

        for j in range(NUM_LINE_SEGMENTS):
            # Calculate the orientation for each path point
            t = float(j) / NUM_LINE_SEGMENTS
            dQtx = 3*((1-t)**2)*(p1['x']-p0['x']) + 6*(1-t)*t*(p2['x']-p1['x']) + 3*(t**2)*(p3['x']-p2['x'])
            dQty = 3*((1-t)**2)*(p1['y']-p0['y']) + 6*(1-t)*t*(p2['y']-p1['y']) + 3*(t**2)*(p3['y']-p2['y'])
            angle_rad = atan2(dQty, dQtx)
            angle_deg = ceil(angle_rad/pi * 180)
            angle_deg = (angle_deg + 360) % 360
            path_angles.append(angle_deg)
        prev_p2 = p2
    return path_points, path_angles

def smoothen_camera(camera_path, camera_angles):
    # The change in camera orientation between two immediate path points can be large
    # if the turning angle is big. Hence we interpolate the orientations between
    # two immediate path points to make the change in oriengation gradual
    final_path_points, final_camera_angles = [], []
    vertical_vector = np.array([0, 0, -1])
    for i in range(1, len(camera_angles)):
        step = 1 if camera_angles[i] > camera_angles[i-1] else -1
        start_range = int(camera_angles[i-1])
        end_range = (int(camera_angles[i]) + 1) if (int(camera_angles[i-1]) - int(camera_angles[i]) == 0) else int(camera_angles[i])
        if abs(camera_angles[i] - camera_angles[i-1]) > 180:
            if camera_angles[i] > camera_angles[i-1]:
                start_range += 360
                end_range = int(camera_angles[i])
                step = -1
            else:
                start_range = int(camera_angles[i]) + 360
                end_range = int(camera_angles[i-1])
                step = 1
        for angle_deg in range(start_range, end_range, step):
            # Interpolate between two orientations to make the camera turning smooth
            optical_vector = np.array([cos(float(angle_deg)/180 * pi), sin(float(angle_deg)/180 * pi), 0])
            horizontal_vector = np.cross(vertical_vector, optical_vector)
            final_camera_angles.append(np.array([horizontal_vector, vertical_vector, optical_vector]))
            final_path_points.append(camera_path[i])
    return final_path_points, final_camera_angles
