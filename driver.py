import math
import cv2
import numpy as np
import torch
import chess
import chess.svg
from math import ceil
from collections import defaultdict, Counter
from PIL import Image


# Segment_by_angle_kmeans, intersection, and segmented_intersections with assistance from Stack Overflow
def segment_by_angle_kmeans(lines, k=2):
    # Settings from OpenCV docs; default k=2 for horizontal and vertical clustering
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    flags = cv2.KMEANS_RANDOM_CENTERS
    attempts = 10

    angles = np.array([line[0][1] for line in lines])
    pts = np.array([[np.cos(2*angle), np.sin(2*angle)]
                    for angle in angles], dtype=np.float32)

    labels, _ = cv2.kmeans(pts, k, None, criteria, attempts, flags)[1:]
    labels = labels.reshape(-1)

    segmented = defaultdict(list)
    for i, line in enumerate(lines):
        segmented[labels[i]].append(line)
    segmented = list(segmented.values())
    return segmented


def intersection(line1, line2):
    rho1, theta1 = line1[0]
    rho2, theta2 = line2[0]
    A = np.array([
        [np.cos(theta1), np.sin(theta1)],
        [np.cos(theta2), np.sin(theta2)]
    ])
    b = np.array([[rho1], [rho2]])

    # Solve the matrix equation for [x, y]
    x0, y0 = np.linalg.solve(A, b)
    x0, y0 = int(np.round(x0)), int(np.round(y0))
    return [[x0, y0]]


def segmented_intersections(lines):
    intersections = []
    for i, group in enumerate(lines[:-1]):
        for next_group in lines[i+1:]:
            for line1 in group:
                for line2 in next_group:
                    i = intersection(line1, line2)
                    intersections.append(i)
    return intersections


def segmented_intersections_with_dict(lines):
    intersections = []
    line_intersections = defaultdict(list)
    # threshold = 10
    for idx, group in enumerate(lines[:-1]):
        for next_group in lines[idx+1:]:
            for line1 in group:
                for line2 in next_group:
                    i = intersection(line1, line2)
                    tup = (line1[0][0], line1[0][1])
                    if not line_intersections[tup]:
                        line_intersections[tup].append(i)
                        continue
                    if abs(i[0][0] - line_intersections[tup][-1][0][0]) > 0 and abs(i[0][1] - line_intersections[tup][-1][0][1]):
                        line_intersections[tup].append(i)
    return intersections, line_intersections


def hough_to_rect(rho, theta, length):
    a = np.cos(theta)
    b = np.sin(theta)
    x0 = a*rho
    y0 = b*rho
    x1 = int(x0 + length*(-b))
    y1 = int(y0 + length*(a))
    x2 = int(x0 - length*(-b))
    y2 = int(y0 - length*(a))

    return x1, y1, x2, y2


def filter_lines(img, lines, length):
    strong_lines = [lines[0]]
    filtered_lines = []

    for line in lines:
        rho, theta = line[0]
        x1, y1, x2, y2 = hough_to_rect(rho, theta, length)

        # Filter lines by their start and end points assuming that the chessboard
        #   is in the bottom center of the screen
        if x1 <= 0 and y1 <= 0 and x2 >= 0 and y2 >= 0:
            if x2 < ceil(0.55 * img.shape[1]) or y2 < ceil(0.55*img.shape[0]):
                continue
        if x1 <= 0 and y1 >= 0 and x2 >= 0 and y2 >= 0:
            if x2 < ceil(0.55 * img.shape[1]) or y2 < ceil(0.55*img.shape[0]):
                continue

        if x2 - x1 == 0:
            continue

        s = (y2 - y1) / (x2 - x1)

        # Get rid of lines that are too vertical or horizontal
        # Need to find a better method since now we can't mark chessboards
        #   head-on
        if abs(s) > 10 or abs(s) < 0.01:
            continue

        # Only accept lines within a certain angle range; problems similar to those above
        if -1*np.pi/3 <= theta <= np.pi/3 or (-1*np.pi/3 - np.pi/4) <= theta <= (np.pi/3 + np.pi/4):
            filtered_lines.append(line)
            continue

        filtered_lines.append(line)

    for line in filtered_lines[1:]:
        append_ = True
        for strong_line in strong_lines:
            strong_rho, strong_theta = strong_line[0]
            rho, theta = line[0]

            # Filter by difference in x-coordinates to get rid of
            #   lines that are too close together; needs work
            strong_x1, strong_y1, strong_x2, strong_y2 = hough_to_rect(
                strong_rho, strong_theta, length)
            x1, y1, x2, y2 = hough_to_rect(rho, theta, length)
            if abs(strong_x2 - x2) < 0.015 * np.sqrt(img.shape[0]**2 + img.shape[1] ** 2):
                if abs(strong_x2 - x2) < 0.7 * np.sqrt(img.shape[0]):
                    append_ = False
                    continue

        if append_:
            strong_lines.append(line)
    return filtered_lines


def process_lines(lines):
    segmented = segment_by_angle_kmeans(lines)
    orientation_to_lines = {
        'vert': segmented[1],
        'horiz': segmented[0]
    }

    # Get rid of outlier lines within each cluster
    vert_outlier = min(segment_by_angle_kmeans(
        segmented[1], k=2), key=len)[0]
    horiz_outlier = min(segment_by_angle_kmeans(
        segmented[0], k=2), key=len)[0]

    for i, segmented_vert in enumerate(orientation_to_lines['vert']):
        rho, theta = segmented_vert[0]
        if rho == vert_outlier[0][0] and theta == vert_outlier[0][1]:
            orientation_to_lines['vert'] = orientation_to_lines['vert'][:i] + \
                orientation_to_lines['vert'][i+1:]

    for i, segmented_horiz in enumerate(orientation_to_lines['horiz']):
        rho, theta = segmented_horiz[0]
        if rho == horiz_outlier[0][0] and theta == horiz_outlier[0][1]:
            orientation_to_lines['horiz'] = orientation_to_lines['horiz'][:i] + \
                orientation_to_lines['horiz'][i+1:]

    return orientation_to_lines


def get_corners(orientation_to_lines, length):

    def by_midpoint(orientation='vert'):
        def calc(line):
            x_temp = line.reshape(-1)
            x1, y1, x2, y2 = hough_to_rect(x_temp[0], x_temp[1], length)
            if orientation == 'vert':
                return (x1 + x2) // 2
            return (y1 + y2) // 2

        return calc

    # Left intersection points = intersection of smallest-x vertical with the extreme horizontal
    #   lines; right intersection points are symmetric
    left = segmented_intersections(
        [[min(orientation_to_lines['vert'], key=by_midpoint('vert'))], [
            max(orientation_to_lines['horiz'], key=by_midpoint('horiz')),
            min(orientation_to_lines['horiz'], key=by_midpoint('horiz'))]
         ])
    right = segmented_intersections(
        [[max(orientation_to_lines['vert'], key=by_midpoint('vert'))], [
            min(orientation_to_lines['horiz'], key=by_midpoint('horiz')),
            max(orientation_to_lines['horiz'], key=by_midpoint('horiz'))]
         ])

    return left + right


def get_all_intersections(orientation_to_lines, length):

    # Left intersection points = intersection of smallest-x vertical with the extreme horizontal
    #   lines; right intersection points are symmetric
    line_intersections = segmented_intersections_with_dict(
        lines=[orientation_to_lines['vert'], orientation_to_lines['horiz']])

    return line_intersections


def draw_lines(img, lines, length):
    filtered_line_img = img
    for line in lines:
        rho, theta = line[0]
        x1, y1, x2, y2 = hough_to_rect(rho, theta, length)
        cv2.line(filtered_line_img, (x1, y1), (x2, y2), (0, 255, 0), 1)


def draw_corners(img, corners):
    for corner in corners:
        x, y = corner
        cv2.circle(img, (int(x), int(y)), radius=2,
                   color=(255, 0, 0), thickness=3)


def draw_segmented_lines(img, orientation_to_lines, length):
    for orientation in orientation_to_lines:
        for segmented_line in orientation_to_lines[orientation]:
            color = (0, 255, 0) if orientation == 'vert' else (0, 0, 255)
            rho, theta = segmented_line[0]
            x1, y1, x2, y2 = hough_to_rect(rho, theta, length)
            cv2.line(img, (x1, y1), (x2, y2), color, 1)


def process_points(points):
    criteria = (cv2.TERM_CRITERIA_EPS, 100, 0.5)
    flags = cv2.KMEANS_PP_CENTERS

    points = [np.array([x[0], x[1]]) for x in points]

    _, labels, centers = cv2.kmeans(
        data=np.array(points, dtype=np.float32), K=81 if len(points) >= 81 else len(points), bestLabels=None, criteria=criteria, attempts=100, flags=flags)
    new_points = set()
    clusters = set()
    for i, point in enumerate(points):
        if labels[i][0] in clusters:
            continue
        clusters.add(labels[i][0])
        x, y = point
        new_points.add((x, y))
    return new_points


def interpolate_vertical_intersections(orientation_to_lines, length, intersections):
    vertical_lines = orientation_to_lines['vert']
    rectangular_lines = list(map(lambda x: hough_to_rect(
        x[0][0], x[0][1], length), vertical_lines))
    sorted_by_x = sorted(rectangular_lines, key=lambda x: x[0])

    distances = []
    for i, line in enumerate(sorted_by_x[:-1]):
        distances.append(abs(line[0] - sorted_by_x[i+1][0]))

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    flags = cv2.KMEANS_RANDOM_CENTERS
    _, labels, centers = cv2.kmeans(
        np.array(distances, dtype=np.float32), 3, None, criteria, 5, flags)

    label_counter = Counter([l[0] for l in labels])
    max_label = max(label_counter, key=label_counter.get)
    mode_average_dist = centers[max_label][0]

    intersections_by_vert = intersections[1]
    right_most_lines = max(
        intersections_by_vert.keys(), key=lambda x: x[0])
    new_points = list(
        map(lambda x: [int(x[0][0] - (mode_average_dist // 3)), int(x[0][1] - math.log(mode_average_dist // 3))], intersections_by_vert[right_most_lines]))

    return new_points


def configure_board(img, points):
    sorted_by_y = sorted(points, key=lambda x: x[1])
    by_row = [sorted(sorted_by_y[i:i + 9], key=lambda x: x[0])
              for i in range(0, len(sorted_by_y), 9)]
    by_column = []
    for i, chunk in enumerate(by_row):
        for j, c in enumerate(chunk):
            x, y = c
            cv2.circle(img, (x + 20, y + 20), radius=2,
                       color=(255, 0, 0), thickness=3)
    for i, chunk in enumerate(by_row):
        by_column.append([])
        for j, c in enumerate(chunk):
            x, y = c
            cv2.circle(img, (x, y), radius=2,
                       color=(0, 255, 0), thickness=3)
            try:
                by_column[-1].append(by_row[j][i])
            except IndexError:
                pass
    return by_row, by_column


def get_position(img, points, bbox_point):
    by_row, by_column = configure_board(img, points)
    file, rank = 1, 1
    for i in range(7):
        if by_row[i][0][1] <= bbox_point[1] <= by_row[i + 1][-1][1]:
            file = i + 1
        if by_column[i][-1][0] <= bbox_point[0] <= by_column[i + 1][0][0]:
            rank = i + 1

    return file, rank


def cls_to_tag(cls):
    mapping = {
        'white-rook': 'R',
        'white-knight': 'N',
        'white-bishop': 'B',
        'white-king': 'K',
        'white-queen': 'Q',
        'white-pawn': 'P',
        'black-pawn': 'p',
        'black-rook': 'r',
        'black-bishop': 'b',
        'black-knight': 'n',
        'black-king': 'k',
        'black-queen': 'q'
    }
    return mapping[cls]


def detect(original_img, img, points):
    original_img = Image.open(original_img)
    model = torch.hub.load('ultralytics/yolov5', 'custom',
                           path='best.pt')
    model.conf = 0.05
    results = model(original_img, size=512)
    keys = []
    for index, row in results.pandas().xyxy[0].iterrows():
        x = (row['xmin'] + row['xmax']) / 2
        x, y = get_position(img, points, (x, row['ymax']))
        keys.append(
            (int(x), int(y), cls_to_tag(row['name'])))

    board = [[""] * 8 for _ in range(8)]
    for key in keys:
        file, rank, char = key
        board[file-1][rank-1] = char

    # generate fen string
    fen_string = ""
    for i, row in enumerate(board):
        num_blanks = 0
        for j, char in enumerate(row):
            if char == '' and j != len(row)-1:
                num_blanks += 1
                continue
            if char == '' and j == len(row)-1:
                num_blanks += 1
            if num_blanks > 0:
                fen_string += str(num_blanks)
            fen_string += char
            num_blanks = 0
        fen_string += "/" if i != len(board)-1 else ''
    board = chess.Board(fen_string)
    print(fen_string)
    print(board)
    chess.svg.board(board, size=350)


def main():
    file_path = 'data/test.png'
    img = cv2.imread(file_path)
    img = cv2.Canny(img, 200, 250, apertureSize=3)
    length = np.sqrt(img.shape[0]**2 + img.shape[1]**2)

    # Hough transform
    lines = cv2.HoughLines(img, 1, np.pi/360, 150)
    if not lines.any():
        print('No lines detected')
        return
    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    lines_img = img.copy()
    draw_lines(lines_img, lines, length)

    # Line processing
    filtered_lines = filter_lines(img, lines, length)
    filtered_line_img = img.copy()
    draw_lines(filtered_line_img, filtered_lines, length)

    orientation_to_lines = process_lines(filtered_lines)
    segmented_lines_img = img.copy()
    draw_segmented_lines(segmented_lines_img, orientation_to_lines, length)

    # Corner processing

    all_intersections = get_all_intersections(orientation_to_lines, length)
    points = set()
    for l in list(all_intersections[1].values()):
        for ls in l:
            x, y = ls[0]
            points.add((x, y))

    new_vertical_intersections = interpolate_vertical_intersections(
        orientation_to_lines, length, all_intersections)

    points.update([(x, y) for x, y in new_vertical_intersections])
    points = process_points(points)
    draw_corners(img, points)

    # Final drawing
    draw_segmented_lines(img, orientation_to_lines, length)

    # Detect pieces
    detect(file_path, img, points)


if __name__ == "__main__":
    main()
