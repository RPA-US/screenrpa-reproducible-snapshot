import os
import cv2
import copy
import numpy as np
import json
from core.settings import sep
from PIL import Image, ImageDraw
from shapely.geometry.base import BaseGeometry
from apps.featureextraction.SOM.Component import Component
from apps.featureextraction.UIFEs.aggregate_features_as_dataset_columns import *
from apps.featureextraction.UIFEs.single_feature_extraction_techniques import *
from apps.featureextraction.PostProcessing.postprocessing_techniques import *
from apps.featureextraction.SOM.screen2som.hierarchy_constructor import labels_to_output
from core.settings import SINGLE_FE_EXTRACTORS_FILEPATH, AGGREGATE_FE_EXTRACTORS_FILEPATH, POSTPROCESSING_TECHNIQUES_FILEPATH
from .models import FeatureExtractionTechnique, Prefilters, Postfilters, UIElementsDetection, UIElementsClassification
from django.shortcuts import get_object_or_404

###########################################################################################################################
# case study get phases data  ###########################################################################################
###########################################################################################################################

def get_prefilters(case_study):
  return get_object_or_404(Prefilters, case_study=case_study, active=True)

def get_ui_elements_detection(case_study):
  return get_object_or_404(UIElementsDetection, case_study=case_study, active=True)

def get_ui_elements_classification(case_study):
  return get_object_or_404(UIElementsClassification, case_study=case_study, active=True)

def get_postfilters(case_study):
  return get_object_or_404(Postfilters, case_study=case_study, active=True)

def get_feature_extraction_technique(case_study):
  return get_object_or_404(FeatureExtractionTechnique, case_study=case_study, active=True)

###########################################################################################################################
# case study has phases data  ###########################################################################################
###########################################################################################################################

# def case_study_has_prefilters(case_study):
#   return Prefilters.objects.filter(case_study=case_study, active=True).exists()

# def case_study_has_ui_elements_detection(case_study):
#   return UIElementsDetection.objects.filter(case_study=case_study, active=True).exists()

# def case_study_has_ui_elements_classification(case_study):
#   return UIElementsClassification.objects.filter(case_study=case_study, active=True).exists()

# def case_study_has_postfilters(case_study):
#   return Postfilters.objects.filter(case_study=case_study, active=True).exists()

# def case_study_has_feature_extraction_technique(case_study):
#   return FeatureExtractionTechnique.objects.filter(case_study=case_study, active=True).exists()


###########################################################################################################################
# Feature extraction techniques ###########################################################################################
###########################################################################################################################

def execution_has_feature_extraction_technique(execution, type="ANY"):
    if type=="SINGLE":
        res = execution.feature_extraction_technique and execution.feature_extraction_technique.type=="SINGLE"
    elif type=="AGGREGATE":
        res = execution.feature_extraction_technique and execution.feature_extraction_technique.type=="AGGREGATE"
    else:
        res = execution.feature_extraction_technique
    return res

def detect_single_fe_function(text):
    '''
    Selecting a function in the system by means of a keyword
    args:
        text: function to be detected
    '''
    # Search the function by key in the json
    f = open(SINGLE_FE_EXTRACTORS_FILEPATH)
    json_func = json.load(f)
    return eval(json_func[text])

def detect_agg_fe_function(text):
    '''
    Selecting a function in the system by means of a keyword
    args:
        text: function to be detected
    '''
    # Search the function by key in the json
    f = open(AGGREGATE_FE_EXTRACTORS_FILEPATH)
    json_func = json.load(f)
    return eval(json_func[text])

def detect_postprocessing_function(text):
    '''
    Selecting a function in the system by means of a keyword
    args:
        text: function to be detected
    '''
    # Search the function by key in the json
    f = open(POSTPROCESSING_TECHNIQUES_FILEPATH)
    json_func = json.load(f)
    return eval(json_func[text])


###########################################################################################################################
# UIED CONFIG            ##################################################################################################
###########################################################################################################################

class Config:

    def __init__(self):

        # *** Frozen ***
        self.THRESHOLD_REC_MIN_EVENNESS = 0.7
        self.THRESHOLD_REC_MAX_DENT_RATIO = 0.25
        self.THRESHOLD_LINE_THICKNESS = 8
        self.THRESHOLD_LINE_MIN_LENGTH = 0.95
        self.THRESHOLD_COMPO_MAX_SCALE = (0.25, 0.98)  # (120/800, 422.5/450) maximum height and width ratio for a atomic compo (button)
        self.THRESHOLD_TEXT_MAX_WORD_GAP = 10
        self.THRESHOLD_TEXT_MAX_HEIGHT = 0.04  # 40/800 maximum height of text
        self.THRESHOLD_TOP_BOTTOM_BAR = (0.045, 0.94)  # (36/800, 752/800) height ratio of top and bottom bar
        self.THRESHOLD_BLOCK_MIN_HEIGHT = 0.03  # 24/800

###########################################################################################################################
# PREPROCESSING          ##################################################################################################
########################################################################################################################### 
def read_img(path, resize_height=None, kernel_size=None):

    def resize_by_height(org):
        w_h_ratio = org.shape[1] / org.shape[0]
        resize_w = resize_height * w_h_ratio
        re = cv2.resize(org, (int(resize_w), int(resize_height)))
        return re

    try:
        img = cv2.imread(path)
        og_shape = img.shape
        if kernel_size is not None:
            img = cv2.medianBlur(img, kernel_size)
        if img is None:
            print("*** Image does not exist ***")
            return None, None
        if resize_height is not None:
            org = resize_by_height(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img, org, gray, og_shape

    except Exception as e:
        print(e)
        print("*** Img Reading Failed ***\n")
        return None, None

def binarization(org, grad_min, show=False, write_path=None, wait_key=0):

    def gray_to_gradient(img):
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img_f = np.copy(img)
        img_f = img_f.astype("float")

        kernel_h = np.array([[0,0,0], [0,-1.,1.], [0,0,0]])
        kernel_v = np.array([[0,0,0], [0,-1.,0], [0,1.,0]])
        dst1 = abs(cv2.filter2D(img_f, -1, kernel_h))
        dst2 = abs(cv2.filter2D(img_f, -1, kernel_v))
        gradient = (dst1 + dst2).astype('uint8')
        return gradient
    
    def grad_to_binary(grad, min):
        rec, bin = cv2.threshold(grad, min, 255, cv2.THRESH_BINARY)
        return bin

    grey = cv2.cvtColor(org, cv2.COLOR_BGR2GRAY)
    grad = gray_to_gradient(grey)        # get RoI with high gradient
    binary = grad_to_binary(grad, grad_min)   # enhance the RoI
    morph = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, (3, 3))  # remove noises
    if write_path is not None:
        cv2.imwrite(write_path, morph)
    if show:
        cv2.imshow('binary', morph)
        if wait_key is not None:
            cv2.waitKey(wait_key)
    return morph



# #######################
# COMPONENT functions copy
# ######################

def compos_update(compos, org_shape, og_shape):
    for i, compo in enumerate(compos):
        # start from 1, id 0 is background
        compo.compo_update(i + 1, org_shape, og_shape)

# #######################
# FILE
# #######################

def save_corners_json(file_path, compos, img_index, texto_detectado_ocr, text_classname, applied_ocr):
    img_shape = compos[0].image_shape
    output = {'img_shape': img_shape, 'compos': []}
    f_out = open(file_path, 'w')

    # Store on global_y all the "y" coordinates and text boxes
    # Each row is a different text box, much more friendly than the format returned by keras_ocr 
    global_y = []
    global_x = []
    words = {}
    words[img_index] = {}

    if applied_ocr:
        for j in range(0, len(texto_detectado_ocr[img_index])):
            coordenada_y = []
            coordenada_x = []

            for i in range(0, len(texto_detectado_ocr[img_index][j][1])):
                coordenada_y.append(texto_detectado_ocr[img_index][j][1][i][1])
                coordenada_x.append(texto_detectado_ocr[img_index][j][1][i][0])

            word = texto_detectado_ocr[img_index][j][0]
            centroid = (np.mean(coordenada_x), np.mean(coordenada_y))
            if word in words[img_index]:
                words[img_index][word] += [centroid]
            else:
                words[img_index][word] = [centroid]

            global_y.append(coordenada_y)
            global_x.append(coordenada_x)
            # print('Coord y, cuadro texto ' +str(j+1)+ str(global_y[j]))
            # print('Coord x, cuadro texto ' +str(j+1)+ str(global_x[j]))

    print("Number of text boxes detected (iteration " + str(img_index) + "): " + str(len(texto_detectado_ocr[img_index]) if applied_ocr else 0))

    # Interval calculation of the text boxes
    intervalo_y = []
    intervalo_x = []
    for j in range(0, len(global_y)):
        intervalo_y.append([int(max(global_y[j])), int(min(global_y[j]))])
        intervalo_x.append([int(max(global_x[j])), int(min(global_x[j]))])

    for compo in compos:
        (x, y, w, h) = compo.put_bbox()
        text = [word for word in words[img_index] if len([coord for coord in words[img_index][word] if x <= coord[0] <= w and y <= coord[1] <= h]) > 0]
        is_text = True if len(text)>0 else False
        c = {'id': compo.id, 'class': compo.category}
        c["text"] = str(' '.join(text)) if is_text else ""
        c["points"] = [(x, y), (w, y), (w, h), (x, h)]
        c["centroid"] = ((x + w) / 2, (y + h) / 2)
        c["xpath"] = []
        c["relevant"] = True
        # (c['column_min'], c['row_min'], c['column_max'], c['row_max']) = (x, y, w, h)
        # c['width'] = compo.width
        # c['height'] = compo.height
        c['contain'] = [contain_compo.id for contain_compo in compo.contain]
        output['compos'].append(c)
    
    output = labels_to_output(copy.deepcopy(output), text_classname)

    json.dump(output, f_out, indent=4)

# ######################
# DETECTION
# ######################

UIEDC = Config()

def nested_components_detection(grey, org, grad_thresh, uied_params,
                   show=False, write_path=None,
                   step_h=10, step_v=10,
                   line_thickness=UIEDC.THRESHOLD_LINE_THICKNESS,
                   min_rec_evenness=UIEDC.THRESHOLD_REC_MIN_EVENNESS,
                   max_dent_ratio=UIEDC.THRESHOLD_REC_MAX_DENT_RATIO):
    '''
    :param grey: grey-scale of original image
    :return: corners: list of [(top_left, bottom_right)]
                        -> top_left: (column_min, row_min)
                        -> bottom_right: (column_max, row_max)
    '''
    compos = []
    mask = np.zeros((grey.shape[0]+2, grey.shape[1]+2), dtype=np.uint8)
    broad = np.zeros((grey.shape[0], grey.shape[1], 3), dtype=np.uint8)
    broad_all = broad.copy()

    row, column = grey.shape[0], grey.shape[1]
    for x in range(0, row, step_h):
        for y in range(0, column, step_v):
            if mask[x, y] == 0:
                # regio1n = flood_fill_bfs(grey, x, y, mask)
                # flood fill algorithm to get background (layout block)
                mask_copy = mask.copy()
                ff = cv2.floodFill(grey, mask, (y, x), None, grad_thresh, grad_thresh, cv2.FLOODFILL_MASK_ONLY)
                # ignore small regions
                if ff[0] < 500: continue
                mask_copy = mask - mask_copy
                region = np.reshape(cv2.findNonZero(mask_copy[1:-1, 1:-1]), (-1, 2))
                region = [(p[1], p[0]) for p in region]

                compo = Component(region, grey.shape)
                # draw.draw_region(region, broad_all)
                # if block.height < 40 and block.width < 40:
                #     continue
                        
                if compo.height < uied_params['nested-min-compo-height']:
                    continue

                # print(block.area / (row * column))
                if compo.area / (row * column) > uied_params['nested-shared-area-percentage']:
                    continue
                elif compo.area / (row * column) > uied_params['nested-shared-area-to-be-redundant']:
                    compo.redundant = True

                # get the boundary of this region
                # ignore lines
                if compo.compo_is_line(line_thickness):
                    continue
                # ignore non-rectangle as blocks must be rectangular
                if uied_params['nested-ignore-non-rectangle-blocks'] or (not compo.compo_is_rectangle(min_rec_evenness, max_dent_ratio)):
                    continue
                
                # if block.height/row < min_block_height_ratio:
                #     continue
                
                compos.append(compo)
                # draw.draw_region(region, broad)
    if show:
        cv2.imshow('flood-fill all', broad_all)
        cv2.imshow('block', broad)
        cv2.waitKey()
    if write_path is not None:
        cv2.imwrite(write_path, broad)
    return compos

def rm_line(binary,
            max_line_thickness=UIEDC.THRESHOLD_LINE_THICKNESS,
            min_line_length_ratio=UIEDC.THRESHOLD_LINE_MIN_LENGTH,
            show=False, wait_key=0):
    def is_valid_line(line):
        line_length = 0
        line_gap = 0
        for j in line:
            if j > 0:
                if line_gap > 5:
                    return False
                line_length += 1
                line_gap = 0
            elif line_length > 0:
                line_gap += 1
        if line_length / width > 0.95:
            return True
        return False

    height, width = binary.shape[:2]
    board = np.zeros(binary.shape[:2], dtype=np.uint8)

    start_row, end_row = -1, -1
    check_line = False
    check_gap = False
    for i, row in enumerate(binary):
        # line_ratio = (sum(row) / 255) / width
        # if line_ratio > 0.9:
        if is_valid_line(row):
            # new start: if it is checking a new line, mark this row as start
            if not check_line:
                start_row = i
                check_line = True
        else:
            # end the line
            if check_line:
                # thin enough to be a line, then start checking gap
                if i - start_row < max_line_thickness:
                    end_row = i
                    check_gap = True
                else:
                    start_row, end_row = -1, -1
                check_line = False
        # check gap
        if check_gap and i - end_row > max_line_thickness:
            binary[start_row: end_row] = 0
            start_row, end_row = -1, -1
            check_line = False
            check_gap = False

    if (check_line and (height - start_row) < max_line_thickness) or check_gap:
        binary[start_row: end_row] = 0

    if show:
        cv2.imshow('no-line binary', binary)
        if wait_key is not None:
            cv2.waitKey(wait_key)
        if wait_key == 0:
            cv2.destroyWindow('no-line binary')

def component_detection(binary, min_obj_area, uied_params,
                        line_thickness=UIEDC.THRESHOLD_LINE_THICKNESS,
                        min_rec_evenness=UIEDC.THRESHOLD_REC_MIN_EVENNESS,
                        max_dent_ratio=UIEDC.THRESHOLD_REC_MAX_DENT_RATIO,
                        rec_detect=False, show=False, test=False):
    """
    Take the binary image as input
    Calculate the connected regions -> get the bounding boundaries of them -> check if those regions are rectangles
    return all boundaries (and boundaries of rectangles if rec_detect=True)
    
    :param binary: Binary image from pre-processing
    :param min_obj_area: If not pass then ignore the small object
    :param min_obj_perimeter: If not pass then ignore the small object
    :param line_thickness: If not pass then ignore the slim object
    :param min_rec_evenness: If not pass then this object cannot be rectangular
    :param max_dent_ratio: If not pass then this object cannot be rectangular
    :return: boundary: [top, bottom, left, right]
                        -> up, bottom: list of (column_index, min/max row border)
                        -> left, right: list of (row_index, min/max column border) detect range of each row
    """
    mask = np.zeros((binary.shape[0] + 2, binary.shape[1] + 2), dtype=np.uint8)
    compos_all = []
    compos_rec = []
    compos_nonrec = []
    row, column = binary.shape[0], binary.shape[1]
    for i in range(0, row, uied_params['step_h']):
        for j in range(i % 2, column, uied_params['step_w']):
            if binary[i, j] == 255 and mask[i, j] == 0:
                # get connected area
                # regio1n = util.boundary_bfs_connected_area(binary, i, j, mask)
                mask_copy = mask.copy()
                ff = cv2.floodFill(binary, mask, (j, i), None, 0, 0, cv2.FLOODFILL_MASK_ONLY)
                if ff[0] < min_obj_area: continue
                mask_copy = mask - mask_copy
                region = np.reshape(cv2.findNonZero(mask_copy[1:-1, 1:-1]), (-1, 2))
                region = [(p[1], p[0]) for p in region]

                # filter out some compos
                component = Component(region, binary.shape)
                # calculate the boundary of the connected area
                # ignore small area
                if component.width <= uied_params["compo-width-considered-small"] or component.height <= uied_params["compo-height-considered-small"]:
                    continue
                # check if it is line by checking the length of edges
                # if component.compo_is_line(line_thickness):
                #     continue

                if test:
                    print('Area:%d' % (len(region)))

                compos_all.append(component)

                if rec_detect:
                    # rectangle check
                    if component.compo_is_rectangle(min_rec_evenness, max_dent_ratio):
                        component.rect_ = True
                        compos_rec.append(component)
                    else:
                        component.rect_ = False
                        compos_nonrec.append(component)

                if show:
                    print('Area:%d' % (len(region)))

    # draw.draw_boundary(compos_all, binary.shape, show=True)
    if rec_detect:
        return compos_rec, compos_nonrec
    else:
        return compos_all

def compo_filter(compos, min_area, img_shape):# TODO: Pablo check filter
    # max_height = img_shape[0] * 0.8
    compos_new = []
    for compo in compos:
        if compo.area < min_area:
            continue
        # DESKTOP: doesnt detect navbars
        # if compo.height > max_height:
        #     continue
        
        # ratio_h = compo.width / compo.height
        # ratio_w = compo.height / compo.width
        # if ratio_h > 50 or ratio_w > 40 or \
        #         (min(compo.height, compo.width) < 8 and max(ratio_h, ratio_w) > 10):
        #     continue
        compos_new.append(compo)
    return compos_new

def merge_intersected_compos(compos):
    changed = True
    while changed:
        changed = False
        temp_set = []
        for compo_a in compos:
            merged = False
            for compo_b in temp_set:
                if compo_a.compo_relation(compo_b) == 2:
                    compo_b.compo_merge(compo_a)
                    merged = True
                    changed = True
                    break
            if not merged:
                temp_set.append(compo_a)
        compos = temp_set.copy()
    return compos

def is_block(clip, thread=0.15):
    '''
    Block is a rectangle border enclosing a group of compos (consider it as a wireframe)
    Check if a compo is block by checking if the inner side of its border is blank
    '''
    side = 4  # scan 4 lines inner forward each border
    if map(lambda c: c<9, clip.shape): return False
    # top border - scan top down
    blank_count = 0
    for i in range(1, 5):
        if sum(clip[side + i]) / 255 > thread * clip.shape[1]:
            blank_count += 1
    if blank_count > 2: return False
    # left border - scan left to right
    blank_count = 0
    for i in range(1, 5):
        if sum(clip[:, side + i]) / 255 > thread * clip.shape[0]:
            blank_count += 1
    if blank_count > 2: return False

    side = -4
    # bottom border - scan bottom up
    blank_count = 0
    for i in range(-1, -5, -1):
        if sum(clip[side + i]) / 255 > thread * clip.shape[1]:
            blank_count += 1
    if blank_count > 2: return False
    # right border - scan right to left
    blank_count = 0
    for i in range(-1, -5, -1):
        if sum(clip[:, side + i]) / 255 > thread * clip.shape[0]:
            blank_count += 1
    if blank_count > 2: return False
    return True

def compo_block_recognition(binary, compos, block_side_length=0.15, color=False):
    if color:
        height, width,_ = binary.shape
    else:
        height, width = binary.shape
    for compo in compos:
        if compo.height / height > block_side_length and compo.width / width > block_side_length:
            clip = compo.compo_clipping(binary)
            if is_block(clip):
                compo.category = 'Block'

def rm_contained_compos_not_in_block(compos):
    '''
    remove all components contained by others that are not Block
    '''
    marked = np.full(len(compos), False)
    for i in range(len(compos) - 1):
        for j in range(i + 1, len(compos)):
            relation = compos[i].compo_relation(compos[j])
            if relation == -1 and compos[j].category != 'Block':
                marked[i] = True
            if relation == 1 and compos[i].category != 'Block':
                marked[j] = True
    new_compos = []
    for i in range(len(marked)):
        if not marked[i]:
            new_compos.append(compos[i])
    return new_compos


def scale_coordinates(coord, src_resolution, dest_resolution):
    src_height, src_width = src_resolution
    dest_height, dest_width = dest_resolution

    x = coord[0]
    y = coord[1]

    scaled_x = int(x * (dest_width / src_width))
    scaled_y = int(y * (dest_height / src_height))

    return (scaled_x, scaled_y)


def draw_ui_compos_borders(exp_path):
    root_path = os.path.join(exp_path + "_results", "components_json")
    arr = os.listdir(root_path)

    if not os.path.exists(root_path):
        os.makedirs(root_path)
    
    for compo_json_filename in arr:
        with open(os.path.join(root_path, compo_json_filename), 'r') as f:
            print(compo_json_filename)
            compo_json = json.load(f)
            
        # Load image
        image = cv2.imread(os.path.join(exp_path, compo_json_filename[:19]))
        
        for compo in compo_json["compos"]:
            # Extract component properties
            # Coordenadas a escalar
            x_min = int(compo['row_min'])
            y_min = int(compo['column_min'])
            x_max = int(compo['row_max'])
            y_max = int(compo['column_max'])
            
            img_shape = list(compo_json["img_shape"])
            
            # Coordenadas de origen y destino
            src_resolution = (img_shape[0], img_shape[1])
            dest_resolution = (1080, 1920)

            # Escalar coordenadas
            x_min, y_min = scale_coordinates((x_min, y_min), src_resolution, dest_resolution)
            x_max, y_max = scale_coordinates((x_max, y_max), src_resolution, dest_resolution)


            # Define border color based on compo ID
            color_id = compo['id'] % 3  # Assuming 3 different colors
            if color_id == 0:
                border_color = (255, 0, 0)  # Blue
            elif color_id == 1:
                border_color = (0, 255, 0)  # Green
            else:
                border_color = (0, 0, 255)  # Red

            # Draw border rectangle on the image
            cv2.rectangle(image, (x_min, y_min), (x_max, y_max), border_color, 2)

            
        # Save the image with component borders
        output_path = os.path.join(exp_path + "_results", "compo_json_borders", compo_json_filename[:19])  # Replace with your desired output file path
        cv2.imwrite(output_path, image)
        image = None


        with open(os.path.join(root_path, compo_json_filename), "w") as outfile:
            json.dump(compo_json, outfile, indent=4)
            
def get_geometry_list_points(coords, geometry):
    if geometry.geom_type == "MultiPolygon" or geometry.geom_type == "GeometryCollection" :
        for g in geometry:
            coords += get_geometry_list_points(coords, g)
    else:
        coords += list(geometry.exterior.coords)
    return coords

def polygon_list_to_draw(polygons, draw: ImageDraw, fill=(255, 0, 0, 128), outline=(255, 0, 0, 255)):
    for geometry in polygons:
        # List points to draw
        coords = get_geometry_list_points([], geometry)
        
        if coords and len(coords) > 0:
            # Draw the geometry on the overlay image
            draw.polygon(coords, fill=fill, outline=outline)

def draw_geometry_over_image(background_image_path, circles, rectangles, output_image_path):
    # Open the background image
    background_image = Image.open(background_image_path)

    # Create a blank image with the same size as the background image
    overlay_image = Image.new('RGBA', background_image.size, (0, 0, 0, 0))

    # Create a drawing context
    draw = ImageDraw.Draw(overlay_image)

    polygon_list_to_draw(rectangles, draw, (255, 255, 0, 128), "blue")
    polygon_list_to_draw(circles, draw)

    # Blend the overlay image with the background image
    result_image = Image.alpha_composite(background_image.convert('RGBA'), overlay_image)

    # Convert the result image to RGB
    result_image_rgb = result_image.convert('RGB')

    # Save the result image
    result_image_rgb.save(output_image_path)


##########################################################################
##    FEATURE EXTRACTION    ##############################################
##########################################################################

def get_compo_from_xpath(path_scenario: str, img_name: str, xpath: list[str]) -> dict | None:
    """
    This function returns the solicitated detected component from the som given an xpath

    params:
        path_scenario: path to the scenario
        img_name: name of the image
        xpath: xpath to the component

    return:
        json with the component information
    """
    som = json.load(open(os.path.join(path_scenario, img_name + ".json")))
    
    # Matching compo does not contain information about the children
    matching_compos = list(filter(lambda x: x["xpath"]==xpath, som["compos"]))

    match(len(matching_compos)):
        case 0:
            return None
        case 1:
            return matching_compos[0]
        case _:
            raise Exception(f"More than one component with the same xpath in img {img_name}, scenario {path_scenario}, xpath {xpath}")