import os
import logging
import json
import math
import datetime
import pandas as pd
import numpy as np
from dateutil import tz
from apps.behaviourmonitoring.log_mapping.eye_tracking_log_processing import add_saccade_index, get_distance_threshold_by_resolution, get_minimum_fixation_gazepoints, int_index, preprocess_gaze_log, rescale_gaze_points
from core.utils import read_ui_log_as_dataframe
from core.settings import MONITORING_IMOTIONS_NEEDED_COLUMNS, INCH_PER_CENTIMETRES, FIXATION_MINIMUM_DURATION, DEVICE_FREQUENCY_WEBGAZER, DEVICE_FREQUENCY_TOBII
from apps.analyzer.utils import convert_timestamps_and_clean_screenshot_name_in_csv, get_csv_log_start_datetime, get_mht_log_start_datetime
from apps.analyzer.utils import format_mht_file
from apps.behaviourmonitoring.log_mapping.eyetracker_log_decoders import decode_timezone, decode_imotions_native_slideevents, decode_imotions_monitoring


ms_pattern = '%H-%M-%S.%f'
# ui_log_timestamp_pattern = '%H:%M:%S %p'
ui_log_timestamp_pattern = '%H:%M:%S'
# ui_log_format_pattern = '\u200e%m/\u200e%d/\u200e%Y %H:%M:%S %p'
ui_log_format_pattern = None

def euclidean_distance(x1, y1, x2, y2):
  """
  Define a function to calculate the Euclidean distance between two points
  """
  return math.sqrt((x2 - x1)**2 + (y2 - y1)**2)

def fixation_dispersion(fixations, gaze_log, x_column_name="Gaze_X", y_column_name="Gaze_Y"):
    """
    Define a function to calculate the fixation dispersion given a list of fixations
    """
    
    # Calculate the mean x and y coordinates of the fixations
    total_x = 0
    total_y = 0
    for index in fixations:
        total_x += float(gaze_log.loc[index,x_column_name])
        total_y += float(gaze_log.loc[index,y_column_name])
    mean_x = total_x / len(fixations)
    mean_y = total_y / len(fixations)

    # Calculate the sum of squared distances from each fixation to the mean fixation
    ssd = sum(euclidean_distance(float(gaze_log.loc[index,x_column_name]), float(gaze_log.loc[index,y_column_name]), mean_x, mean_y)**2 for index in fixations)

    # Divide the sum of squared distances by the number of fixations minus 1 to get the variance
    if len(fixations) > 1:
      variance = ssd / (len(fixations) - 1)
    else:
      variance = 0

    # Take the square root of the variance to get the standard deviation, which is the fixation dispersion
    fixation_dispersion = math.sqrt(variance)

    return fixation_dispersion
  
def calculate_dispersion(gaze_log, metrics, last_index):
  #Dispersion de https://link.springer.com/article/10.3758/s13428-020-01392-6
    current_fixations = range(metrics["start_index"], last_index + 1)
    fixations_x = [float(gaze_log.loc[index,"Gaze_X"]) for index in current_fixations]
    fixations_y = [float(gaze_log.loc[index,"Gaze_Y"]) for index in current_fixations]
    # REF PyTrack: An end-to-end analysis toolkit for eye tracking -> Parameter extraction - Fixations
    dispersion = euclidean_distance(min(fixations_x), min(fixations_y), max(fixations_x), max(fixations_y))
    
    metrics["last_index"] = last_index
    metrics["dispersion"] = dispersion
    return metrics

def get_timestamp(time_begining, start_datetime, current_timestamp, pattern):
  if pattern == "ms":
    ms = float(current_timestamp) / 1000
    min = int(ms/60)
    hs = int(min/60)
    ms -= min*60
    min -= hs*60
    # Datetime can read 6 digits as miliseconds
    if len(str(float(current_timestamp) / 1000)) > 6:
      ms = str(ms)[:6]
    ms = str(hs)+"-"+str(min)+"-"+str(ms)
    time = datetime.datetime.strptime(ms, ms_pattern).time()
    res = start_datetime + datetime.timedelta(hours=time.hour, minutes=time.minute, seconds=time.second, microseconds=time.microsecond)
    total_seconds = (res - time_begining).total_seconds()
  elif pattern == "ms_webgazer":
    ms = float(current_timestamp) / 1000
    min = int(ms/60)
    hs = int(min/60)
    ms -= min*60
    min -= hs*60
    # # Datetime can read 6 digits as miliseconds
    # if len(str(float(current_timestamp) / 1000)) > 6:
    #   ms = str(ms)[:6]
    ms = round(ms, 3)  # Round to 3 decimal places
    ms = str(hs)+"-"+str(min)+"-"+str(ms)
    time = datetime.datetime.strptime(ms, ms_pattern).time()
    res = start_datetime + datetime.timedelta(hours=time.hour, minutes=time.minute, seconds=time.second, microseconds=time.microsecond)
    total_seconds = (res - time_begining).total_seconds()
  else:
    time =  datetime.datetime.strptime(current_timestamp, pattern).time()
    res = datetime.datetime.combine(start_datetime, time)
    total_seconds = (res - time_begining).total_seconds()
  if total_seconds < 0:
    raise Exception("Timestamps are not well synchronized")
  return total_seconds, res


def format_fixation_point_key(i, gaze_log):
  return str(gaze_log.iloc[i]["Fixation X"]) + "#" + str(gaze_log.iloc[i]["Fixation Y"])

def gaze_log_get_key(i, gaze_log, gaze_timestamp):
  init = {  
    "#events": 1,
    "timestamp": gaze_timestamp.strftime(ui_log_timestamp_pattern),
    "start_index": i,
    "ms_start": gaze_log.iloc[i]["Fixation Start"],
    "ms_end": gaze_log.iloc[i]["Fixation End"],
    "duration": gaze_log.iloc[i]["Fixation Duration"], 
    # "imotions_dispersion": gaze_log.iloc[i]["Fixation Dispersion"]
  }
  return format_fixation_point_key(i, gaze_log), init

def update_previous_screenshots_in_splitted_events(fixation_points, j, key, init, ui_log, last_counter, special_colnames):
  for k in range(1, last_counter+1):
    if not (ui_log.iloc[j-k][special_colnames["Screenshot"]] in fixation_points):
      # Initialize gaze metrics
      fixation_points[ui_log.iloc[j-k][special_colnames["Screenshot"]]] = { 'fixation_points': { key: init} }
    elif key in fixation_points[ui_log.iloc[j-k][special_colnames["Screenshot"]]]["fixation_points"]:
      fixation_points[ui_log.iloc[j-k][special_colnames["Screenshot"]]]["fixation_points"][key]["#events"] += 1
    else:
      # Initialize gaze metrics
      #La clave "intersectioned" indica que el punto de fijación intersecciona dos capturas de pantalla, que se corresponden con dos eventos diferentes.
      # El intersectioned estara a True cuando haya un evento de gaze cuyo timestamp se encuentre entre el timestamp del ultimo evento (del UI Log) ocurrido en una captura y el timestamp del primer evento (ui log) de la siguiente captura
      fixation_points[ui_log.iloc[j-k][special_colnames["Screenshot"]]]["fixation_points"][key] = init
    fixation_points[ui_log.iloc[j-k][special_colnames["Screenshot"]]]["fixation_points"][key]["intersectioned"] = "True"
  
  return fixation_points 

def update_fixation_points(j, i, key, fixation_points, gaze_log, ui_log, last_fixation_index, last_gaze_log_row, last_fixation_index_row, last_ui_log_index_row, starting_point, initial_timestamp, current_timestamp, startDateTime_ui_log, startDateTime_gaze_tz, special_colnames, gaze_log_timestamp_pattern):
  """
  
  Update fixation points values
  
  j: ui_log index
  i: gaze_log index
  fixation_points
  gaze_log: gaze_log content
  ui_log: ui_log content
  last_fixation_index: fixation index of the last row
  last_gaze_log_row: last gaze log row 
  last_fixation_index_row: gaze row index of the last fixation point detected
  last_ui_log_index_row: ui log index that point to last fixation related event
  starting_point: the start time to measure timestamps
  initial_timestamp: first timestamp in the log
  current_timestamp: current timestamp
  startDateTime_ui_log: start datetime in ui log
  startDateTime_gaze_tz: start datetime in gaze log
  special_colnames: json indicating special columns names
  """
  gaze_fixation_start = None
  
  # When a fixation slot ends, dispersion is calculated
  if last_ui_log_index_row != -1 and (not pd.isnull(last_fixation_index)) and \
    last_fixation_index != 0 and (not pd.isnull(gaze_log.iloc[i]["Fixation Index"])) \
    and gaze_log.iloc[i]["Fixation Index"] != last_fixation_index \
    and format_fixation_point_key(last_fixation_index_row, gaze_log) in fixation_points[ui_log.iloc[last_ui_log_index_row][special_colnames["Screenshot"]]]["fixation_points"]:
    screenshot_name = ui_log.iloc[last_ui_log_index_row][special_colnames["Screenshot"]]
    gaze_metrics = fixation_points[screenshot_name]["fixation_points"][format_fixation_point_key(last_fixation_index_row, gaze_log)]
    metrics_aux = calculate_dispersion(gaze_log, gaze_metrics, last_fixation_index_row)
    fixation_points[screenshot_name]["fixation_points"][format_fixation_point_key(last_fixation_index_row, gaze_log)] = metrics_aux
  
  # if fixation_key is in screenshot fixation_points and fixation_index is equal to last_fixation_index
  if key and (key in fixation_points[ui_log.iloc[j][special_colnames["Screenshot"]]]["fixation_points"]) and \
    gaze_log.iloc[i]["Fixation Index"] == last_fixation_index:
    # increase the number of events associated to that fixation + 1
    fixation_points[ui_log.iloc[j][special_colnames["Screenshot"]]]["fixation_points"][key]["#events"] += 1
  # if an UI log event splits a fixation cluster:  
  # if it is the first iteration, and 
  # the row contains fixation, and 
  # dict contains fixation key, and 
  # ui log event screenshot is distinct that the screenshot of the last event
  elif (i == last_gaze_log_row) and \
    gaze_log.iloc[i]["Fixation Start"] and (not pd.isnull(gaze_log.iloc[i]["Fixation Start"])) and \
    (ui_log.iloc[j-1][special_colnames["Screenshot"]] in fixation_points and \
    "fixation_points" in fixation_points[ui_log.iloc[j-1][special_colnames["Screenshot"]]] and \
    key in fixation_points[ui_log.iloc[j-1][special_colnames["Screenshot"]]]["fixation_points"]) and \
      ui_log.iloc[j][special_colnames["Screenshot"]] != ui_log.iloc[j-1][special_colnames["Screenshot"]]:
      # raise an exception
      logging.exception("behaviourmonitoring/monitoring/update_fixation_points line:65. UI Log row " + str(j) + ". Fixation cluster splitted by two UI Log event!")
      raise Exception("Fixation cluster splitted by two UI Log event!")
  else:
    fixation_start = gaze_log.iloc[i]["Fixation Start"]
    if fixation_start and (not pd.isnull(fixation_start)):
      gaze_fixation_start, t = get_timestamp(starting_point, startDateTime_gaze_tz, fixation_start, gaze_log_timestamp_pattern) # + gaze_log_timedelta
      key, init = gaze_log_get_key(i, gaze_log, t)
      last_counter = 1
      aux_index = j-last_counter if j != 0 else j
      last_timestamp, t = get_timestamp(starting_point, startDateTime_ui_log, ui_log.iloc[aux_index][special_colnames["Timestamp"]], ui_log_timestamp_pattern)
      
      while last_timestamp == current_timestamp and j != 0:
        last_timestamp, t = get_timestamp(starting_point, startDateTime_ui_log, ui_log.iloc[j-last_counter][special_colnames["Timestamp"]], ui_log_timestamp_pattern)
        logging.info("behaviourmonitoring/monitoring/update_fixation_points line:76. UI Log row " + str(j) + ". Finding the last timestamp change: " + str(last_counter) + " event before")
        last_counter+=1
      
      if gaze_fixation_start >= current_timestamp:
        if key in fixation_points[ui_log.iloc[j][special_colnames["Screenshot"]]]["fixation_points"]:
          fixation_points[ui_log.iloc[j][special_colnames["Screenshot"]]]["fixation_points"][key]["#events"] += 1
        else:
          # Initialize gaze metrics
          fixation_points[ui_log.iloc[j][special_colnames["Screenshot"]]]["fixation_points"][key] = init
      elif (last_timestamp <= gaze_fixation_start) and (gaze_fixation_start < current_timestamp):
        fixation_points = update_previous_screenshots_in_splitted_events(fixation_points, j, key, init, ui_log, last_counter, special_colnames)
      else:
        logging.info("behaviourmonitoring/monitoring/update_fixation_points line:87. Gaze log event PREVIOUS to UI log capture, gaze log row number:" + str(gaze_log.iloc[i]["RowNumber"]))
        
        if key and (key in fixation_points["previous"]):
          fixation_points["previous"][key]["#events"] += 1
        else:
          # Initialize gaze metrics
          fixation_points["previous"][key] = init
    
    else:
      if gaze_log.iloc[i]["Saccade Index"] and (not pd.isnull(gaze_log.iloc[i]["Saccade Index"])):
        msg = "Row " + str(gaze_log.iloc[i]["RowNumber"]) + ": Saccade movement - Index " + str(gaze_log.iloc[i]["Saccade Index"])
      else:
        msg = "No fixation point in " + str(gaze_log.iloc[i]["RowNumber"]) + ". Saccade: " + str(gaze_log.iloc[i]["Saccade Index"])
      logging.info(msg)
      print(msg)
  
  if not pd.isnull(gaze_log.iloc[i]["Fixation Index"]):
    last_fixation_index = gaze_log.iloc[i]["Fixation Index"]
    last_fixation_index_row = i
    if gaze_fixation_start and gaze_fixation_start >= current_timestamp:
      last_ui_log_index_row = j
  
  return fixation_points, key, last_fixation_index, last_fixation_index_row, last_ui_log_index_row, last_gaze_log_row

def gaze_log_mapping(ui_log, gaze_log, special_colnames, startDateTime_ui_log, startDateTime_gaze_tz, gaze_log_timestamp_pattern):
  # https://imotions.com/release-notes/imotions-9-1-7/
  startDateTime_gaze_tz = startDateTime_gaze_tz.replace(tzinfo=None)

  if startDateTime_ui_log > startDateTime_gaze_tz:
    starting_point = startDateTime_gaze_tz
  elif startDateTime_ui_log < startDateTime_gaze_tz:
    starting_point = startDateTime_ui_log
  else:
    starting_point = startDateTime_gaze_tz
    logging.info("behaviourmonitoring/monitoring/gaze_log_mapping line:109. Gaze Log and UI Log already synchronized!")

  # TODO: we suppose that dataframe timestamps are ordered
  # ui_log = ui_log.sort_values(special_colnames["Timestamp"])
  # gaze_log = gaze_log.sort_values("Timestamp")

  fixation_points = {"previous": {}, "subsequent": {} }
  
  last_gaze_log_row = 0
  last_fixation_index = 0
  last_fixation_index_row = -1
  last_ui_log_index_row = -1
  
  initial_timestamp, t = get_timestamp(starting_point, startDateTime_ui_log, ui_log.iloc[0][special_colnames["Timestamp"]], ui_log_timestamp_pattern)
  
  # Loop: Each UI Log row
  for j in range(len(ui_log)):
      print("Processing "+ui_log.iloc[j][special_colnames["Screenshot"]]+" out of "+str(len(ui_log))+" screenshots.")
      print("UI_Log Size"+str(len(ui_log)))
      
      # Obtain current event timestamp and next event timestamp 
      current_timestamp = ui_log.iloc[j][special_colnames["Timestamp"]]
      current_timestamp, t = get_timestamp(starting_point, startDateTime_ui_log, current_timestamp, ui_log_timestamp_pattern)# + ui_log_timedelta
      if j < (len(ui_log)-1):
        next_timestamp = ui_log.iloc[j+1][special_colnames["Timestamp"]]
        next_timestamp, t = get_timestamp(starting_point, startDateTime_ui_log, next_timestamp, ui_log_timestamp_pattern)# + ui_log_timedelta
      
      #   next_timestamp = ui_log.iloc[j][special_colnames["Timestamp"]]
      #   next_timestamp, t = get_timestamp(starting_point, startDateTime_ui_log, next_timestamp, ui_log_timestamp_pattern)# + ui_log_timedelta
      # if j+1 < len(ui_log):
      #   next_timestamp = ui_log.iloc[j+1][special_colnames["Timestamp"]]
      #   next_timestamp, t = get_timestamp(starting_point, startDateTime_ui_log, next_timestamp, ui_log_timestamp_pattern)# + ui_log_timedelta
      
        if next_timestamp > current_timestamp:
          fixation_points[ui_log.iloc[j][special_colnames["Screenshot"]]] = { 'fixation_points': {} }
          key = None
        
          for i in range(last_gaze_log_row, len(gaze_log)-1):
            gaze_timestamp, t = get_timestamp(starting_point, startDateTime_gaze_tz, gaze_log.iloc[i]["Timestamp"], gaze_log_timestamp_pattern)# + gaze_log_timedelta
          
            # Gaze Event between current ui log event and next ui log event
            if gaze_timestamp < next_timestamp:
              fixation_points, key, last_fixation_index, last_fixation_index_row, last_ui_log_index_row, last_gaze_log_row = update_fixation_points(j, i, key, fixation_points, gaze_log, ui_log, last_fixation_index, last_gaze_log_row, last_fixation_index_row, last_ui_log_index_row, starting_point, initial_timestamp, current_timestamp, startDateTime_ui_log, startDateTime_gaze_tz, special_colnames, gaze_log_timestamp_pattern)
            
            # Gaze Event before current ui log event
            # elif current_timestamp > gaze_timestamp:
            #   raise Exception("current_timestamp > gaze_timestamp")
            # Gaze Event after current ui log event and next ui log event
            else:
              last_gaze_log_row = i
              break
        # Add the dispersion calculation of the last screenshot associated to the UI Log Event
      #LAST SCREENSHOT FROM UI LOG   
      elif j == (len(ui_log)-1):
        fixation_points[ui_log.iloc[j][special_colnames["Screenshot"]]] = { 'fixation_points': {} }
        key = None  
        for i in range(last_gaze_log_row, len(gaze_log)-1):
          gaze_timestamp, t = get_timestamp(starting_point, startDateTime_gaze_tz, gaze_log.iloc[i]["Timestamp"], gaze_log_timestamp_pattern)# + gaze_log_timedelta
          
          # Gaze Event between current ui log event and this LAST ui log event.
          if gaze_timestamp > current_timestamp:  
            fixation_points, key, last_fixation_index, last_fixation_index_row, last_ui_log_index_row, last_gaze_log_row = update_fixation_points(j, i, key, fixation_points, gaze_log, ui_log, last_fixation_index, last_gaze_log_row, last_fixation_index_row, last_ui_log_index_row, starting_point, initial_timestamp, current_timestamp, startDateTime_ui_log, startDateTime_gaze_tz, special_colnames, gaze_log_timestamp_pattern)
            
          else:
            last_gaze_log_row = i
            break        

        screenshot_name = ui_log.iloc[last_ui_log_index_row][special_colnames["Screenshot"]]
        if fixation_points[screenshot_name]["fixation_points"] == {}:
          raise Exception("No fixation points in screenshot " + screenshot_name)
        gaze_metrics = fixation_points[screenshot_name]["fixation_points"][format_fixation_point_key(last_fixation_index_row, gaze_log)]
        metrics_aux = calculate_dispersion(gaze_log, gaze_metrics, last_fixation_index_row)
        fixation_points[screenshot_name]["fixation_points"][format_fixation_point_key(last_fixation_index_row, gaze_log)] = metrics_aux
          
        # If all gaze log events have been covered: break
        if i == len(gaze_log)-2:
          break
      elif next_timestamp < current_timestamp:
        logging.exception("behaviourmonitoring/monitoring/gaze_log_mapping line:152. UI and Gaze Logs Timestamps are not well synchronized, next_timestamp (row " + str(j+1) + ") < current_timestamp (row " + str(j) + "): UI Log Current Screenshot " + ui_log.iloc[j][special_colnames["Screenshot"]])
        raise Exception("UI and Gaze Logs Timestamps are not well synchronized")
      else:
        logging.info("behaviourmonitoring/monitoring/gaze_log_mapping line:155. UI Logs events with the same timestamps: next_timestamp (row " + str(j+1) + ") == current_timestamp (row " + str(j) + ")")
  last_ui_log_timestamp, t = get_timestamp(starting_point, startDateTime_ui_log, ui_log.iloc[len(ui_log)-1][special_colnames["Timestamp"]], ui_log_timestamp_pattern)        
  last_gaze_timestamp, t = get_timestamp(starting_point, startDateTime_gaze_tz, gaze_log.iloc[len(gaze_log)-1]["Timestamp"], gaze_log_timestamp_pattern)# + gaze_log_timedelta
  
  # Store gaze logs that takes place after last UI log event
  if last_gaze_timestamp > last_ui_log_timestamp:
    logging.info("behaviourmonitoring/monitoring/gaze_log_mapping line:161. Gaze log events after UI Log last event")
    for i in range(last_gaze_log_row, len(gaze_log)-1):
      fixation_start = gaze_log.iloc[i]["Fixation Start"]
      if fixation_start and (not pd.isnull(fixation_start)):
        gaze_fixation_start, t = get_timestamp(starting_point, startDateTime_gaze_tz, fixation_start, gaze_log_timestamp_pattern) # + gaze_log_timedelta
        key, init = gaze_log_get_key(i, gaze_log, t)
        if key and (key in fixation_points["subsequent"]):
          fixation_points["subsequent"][key]["#events"] += 1
        else:  
          fixation_points["subsequent"][key] = init
      else:
        if gaze_log.iloc[i]["Saccade Index"] and (not pd.isnull(gaze_log.iloc[i]["Saccade Index"])):
          msg = "Row " + str(gaze_log.iloc[i]["RowNumber"]) + ": Saccade movement - Index " + str(gaze_log.iloc[i]["Saccade Index"])
        else:
          msg = "No fixation point in " + str(gaze_log.iloc[i]["RowNumber"]) + ". Saccade: " + str(gaze_log.iloc[i]["Saccade Index"])
        logging.info(msg)
        print(msg)
    
    #Guardamos aquellos eventos de fijación que no se han guardado en la iteración anterior
    for j in range(len(ui_log)):
      screenshot_name = ui_log.iloc[j][special_colnames["Screenshot"]]
      if screenshot_name not in fixation_points:
        # Añade screenshot_name a fixation_points con un diccionario que contiene "fixation_points" vacío
        fixation_points[screenshot_name] = {"fixation_points": {}}
        # Log para indicar que se ha añadido una nueva entrada para screenshot_name
        logging.info(f"Added {screenshot_name} to fixation_points with empty fixation_points.")    

  
  return fixation_points

def fixation_json_to_dataframe(ui_log, fixation_p, special_colnames, root_path):
  ub_log = ui_log.copy()
  acum = 0
  columns_added = [special_colnames["Screenshot"], special_colnames["NameApp"]]
  var_act_case_bool = [False,False,False]
  if special_colnames["Variant"] in ui_log.columns:
    var_act_case_bool[0] = True
    columns_added.append(special_colnames["Variant"])
  if special_colnames["Activity"] in ui_log.columns:
    var_act_case_bool[1] = True
    columns_added.append(special_colnames["Activity"])
  if special_colnames["Case"] in ui_log.columns:
    var_act_case_bool[2] = True
    columns_added.append(special_colnames["Case"])
  
  columns_nan = ui_log.columns
  columns_nan = columns_nan.drop(columns_added)
  
  for ui_event_index in range(len(ui_log)):
    print("Processing fixation.json to dataframe. ui_event_index: "+str(ui_event_index)+" out of "+str((len(ui_log)-1))+" ## "+str(int(ui_event_index*100/(len(ui_log)-1))) + "% ##")
    new_row_json = {}
    screenshot = ui_log.iloc[ui_event_index][special_colnames["Screenshot"]]
    new_row_json[special_colnames["Screenshot"]] = [screenshot]
    nameapp = ui_log.iloc[ui_event_index][special_colnames["NameApp"]]
    new_row_json[special_colnames["NameApp"]] = [nameapp]
    if var_act_case_bool[0]:
      variant = ui_log.iloc[ui_event_index][special_colnames["Variant"]]
      new_row_json[special_colnames["Variant"]] = [variant]
    if var_act_case_bool[1]:
      activity = ui_log.iloc[ui_event_index][special_colnames["Activity"]]
      new_row_json[special_colnames["Activity"]] = [activity]
    if var_act_case_bool[2]:
      case = ui_log.iloc[ui_event_index][special_colnames["Case"]]
      new_row_json[special_colnames["Case"]] = [case]

    for col in columns_nan:
      new_row_json[col] = [np.nan]
    
    new_row_json[special_colnames["EventType"]] = ["GazeFixation"]
    
    if screenshot in fixation_p: 
      for coor_coded in fixation_p[screenshot]["fixation_points"]:
        coordinates = coor_coded.split("#")
        new_row_json[special_colnames["CoorX"]] = [coordinates[0]]
        new_row_json[special_colnames["CoorY"]] = [coordinates[1]]
        new_row_json["#events"] = [fixation_p[screenshot]["fixation_points"][coor_coded]["#events"]]
        new_row_json[special_colnames["Timestamp"]] = [fixation_p[screenshot]["fixation_points"][coor_coded]["timestamp"]]
        # new_row_json["dispersion"] = [fixation_p[screenshot]["fixation_points"][coor_coded]["dispersion"]]
        # new_row_json["imotions_dispersion"] = [fixation_p[screenshot]["fixation_points"][coor_coded]["imotions_dispersion"]]
        
        new_row_json = pd.DataFrame(new_row_json)
        
        ub_log = pd.concat([ub_log.iloc[:ui_event_index+acum+1], new_row_json, ub_log.iloc[ui_event_index+acum+1:]]).reset_index(drop=True)
        acum+=1
      

  ub_log.to_csv(os.path.join(root_path , "ub_log_fixation.csv"))

def monitoring(log_path, root_path, execution):
  
    special_colnames = execution.case_study.special_colnames
    monitoring_obj = execution.monitoring
    monitoring_type = monitoring_obj.type
    monitoring_screen_inches = monitoring_obj.screen_inches
    monitoring_observer_camera_distance = monitoring_obj.observer_camera_distance
    monitoring_screen_width = monitoring_obj.screen_width
    monitoring_screen_height = monitoring_obj.screen_height
    ui_log_filename = monitoring_obj.ui_log_filename
    monitoring_native_slide_events = monitoring_obj.native_slide_events
    
    if os.path.exists(log_path):
      logging.info("apps/behaviourmonitoring/log_mapping/gaze_monitoring.py Log already exists, it's not needed to execute format conversor")
      print("Log already exists, it's not needed to execute format conversor")
    elif (getattr(monitoring_obj, 'format') is not None):
      log_filename = "log"
      # TODO: org:resource
      log_path = format_mht_file(os.path.join(root_path, ui_log_filename), monitoring_obj.format, root_path, log_filename, 'User1')
  
    ui_log = read_ui_log_as_dataframe(log_path)
    eyetracking_log_filename = monitoring_obj.gaze_log_filename
    
    if monitoring_type == "imotions":
      if eyetracking_log_filename and os.path.exists(os.path.join(root_path ,eyetracking_log_filename)):
          gazeanalysis_log = read_ui_log_as_dataframe(os.path.join(root_path , eyetracking_log_filename))
      else:
          logging.exception("behaviourmonitoring/monitoring/monitoring line:180. Eyetracking log cannot be read: " + root_path + eyetracking_log_filename)
          raise Exception("Eyetracking log cannot be read: " + root_path + eyetracking_log_filename)
        # fixation.json to Dataframe checker
      for col_name in MONITORING_IMOTIONS_NEEDED_COLUMNS:
        if special_colnames[col_name] not in ui_log.columns:
          logging.error("Your UI log doesn't have a column representing : " + col_name + ". It must store information about " + str(MONITORING_IMOTIONS_NEEDED_COLUMNS))
          raise Exception("Your UI log doesn't have a column representing : " + col_name + ". It must store information about " + str(MONITORING_IMOTIONS_NEEDED_COLUMNS))
        
      #GazeLog se corresponde con la tabla GazeLog del fichero de salida de iMotions; Metadata se corresponde con los metadatos que se encuentran en el mismo archivo (datos "feos" que salen arriba de la tabla)
      gaze_log, metadata = decode_imotions_monitoring(gazeanalysis_log)
        
      #Es la información de base de la zona horaria donde se esta llevando a cabo la grabación. (ej:UTC+1)
      startDateTime_gaze_tz = decode_imotions_native_slideevents(root_path, monitoring_obj.native_slide_events)#en el imotions
      startDateTime_ui_log = get_mht_log_start_datetime(os.path.join(root_path, ui_log_filename), ui_log_format_pattern)#en steprecorder

      if os.path.exists(os.path.join(root_path ,"fixation.json")):
        fixation_p = json.load(open(os.path.join(root_path ,"fixation.json")))
        logging.warning("The file " + root_path + "fixation.json already exists. Not regenerated")
        print("The file " + root_path + "fixation.json already exists. If you want to regenerate it, please remove it or change its name")
      else:
        fixation_p = gaze_log_mapping(ui_log, gaze_log, special_colnames, startDateTime_ui_log, startDateTime_gaze_tz, 'ms')
        
      # Serializing json
      json_object = json.dumps(fixation_p, indent=4)
      with open(os.path.join(root_path , "fixation.json"), "w") as outfile:
          outfile.write(json_object)
      logging.info("behaviourmonitoring/monitoring/monitoring. fixation.json saved!")
        
      fixation_json_to_dataframe(ui_log, fixation_p, special_colnames, root_path)
        
      monitoring_obj.executed = 100
      monitoring_obj.ub_log_path = os.path.join(root_path,"fixation.json")
      # update monitoring_obj
      monitoring_obj.save()
    elif monitoring_type == "webgazer":
      #### Postprocessing WebgazerLog Start ####
      pixels_threshold_i_dt = get_distance_threshold_by_resolution(monitoring_screen_inches,INCH_PER_CENTIMETRES, monitoring_observer_camera_distance,monitoring_screen_width,monitoring_screen_height) #Capturing the distance threshold in pixels regarding to the screen resolution
      minimum_fixation_gazepoints = get_minimum_fixation_gazepoints(DEVICE_FREQUENCY_WEBGAZER, FIXATION_MINIMUM_DURATION) #Capturing the minimum number of gazepoints to consider a fixation

      if eyetracking_log_filename and os.path.exists(os.path.join(root_path , eyetracking_log_filename)):
          preprocessed_webgazer_log = read_ui_log_as_dataframe(os.path.join(root_path , eyetracking_log_filename))
      else:
          logging.exception("behaviourmonitoring/monitoring/monitoring line:180.  Eyetracking  webgazer log  cannot be read: " + root_path + eyetracking_log_filename)
          raise Exception("Eyetracking log cannot be read: " + root_path + eyetracking_log_filename)
      rescaled_webgazer_log = rescale_gaze_points(preprocessed_webgazer_log, monitoring_screen_width, monitoring_screen_height)# Rescale the WebGazer Log to the screen resolution
      postprocessed_webgazer_fixations_log_build = preprocess_gaze_log(rescaled_webgazer_log, "Gaze_X", "Gaze_Y",minimum_fixation_gazepoints,pixels_threshold_i_dt)# First Preprocesing of the WebGazer Log. In this Log, we get the fixations (Timestamps, duration, start,end, x,y, etc.)
      postprocessed_webgazer_fixations_saccade_log_build = add_saccade_index(postprocessed_webgazer_fixations_log_build)# Second Preprocesing of the WebGazer Log. In this Log, we get the saccades (Timestamps, duration, start,end, x,y, etc.)
      postprocessed_webgazer_fixations_saccade_index_log_build = int_index(postprocessed_webgazer_fixations_saccade_log_build)#Last Preprocesing of the WebGazer Log. In this Log, we get the index of the fixations and saccades to finally get the gazeanalysis Log (Idem to imotions log)
      postprocessed_webgazer_fixations_saccade_index_log_build.to_csv(os.path.join(root_path , "webgazer_gazedata_postprocessed.csv"))
      ##### Postprocessing WebGazer Log End #####
      if eyetracking_log_filename and os.path.exists(os.path.join(root_path ,"webgazer_gazedata_postprocessed.csv")):
          gazeanalysis_log = read_ui_log_as_dataframe(os.path.join(root_path , "webgazer_gazedata_postprocessed.csv"))
      else:
          logging.exception("behaviourmonitoring/monitoring/monitoring line:180. Eyetracking  webgazerlog cannot be read: " + root_path + "webgazer_gazedata_postprocessed.csv")
          raise Exception("Eyetracking log cannot be read: " + root_path + eyetracking_log_filename)
        # fixation.json to Dataframe checker
      # fixation.json to Dataframe checker        
      for col_name in MONITORING_IMOTIONS_NEEDED_COLUMNS:
        if special_colnames[col_name] not in ui_log.columns:
          logging.error("Your UI log doesn't have a column representing : " + col_name + ". It must store information about " + str(MONITORING_IMOTIONS_NEEDED_COLUMNS))
          raise Exception("Your UI log doesn't have a column representing : " + col_name + ". It must store information about " + str(MONITORING_IMOTIONS_NEEDED_COLUMNS))
        
        #GazeLog se corresponde con la tabla GazeLog del fichero de salida de iMotions; Metadata se corresponde con los metadatos que se encuentran en el mismo archivo (datos "feos" que salen arriba de la tabla)
        #GAZELOG = WEBGAZERLOG.csv debido a que no hay que formartear metadata. columnas de webgazerlog.csv iguales a imotions.
        
      #Es la información de base de la zona horaria donde se esta llevando a cabo la grabación. (ej:UTC+1)
      startDateTime_gaze_tz = decode_timezone(root_path, monitoring_native_slide_events)
      
      #If the ui log file is a mht file. We need to get the startdatetime from the mht file with the get_mht_log_start_datetime function
      if ui_log_filename.endswith('.mht'):
        startDateTime_ui_log = get_mht_log_start_datetime(os.path.join(root_path , ui_log_filename), ui_log_format_pattern)
      #If the ui log file is a csv file. We need to get the startdatetime from the csv file with the get_csv_log_start_datetime function
      elif ui_log_filename.endswith('.csv'):
        startDateTime_ui_log = get_csv_log_start_datetime(os.path.join(root_path , ui_log_filename), ui_log_format_pattern)
        ui_log = convert_timestamps_and_clean_screenshot_name_in_csv(log_path)
        
        
        
      if os.path.exists(os.path.join(root_path ,"fixation.json")):
        fixation_p = json.load(open(os.path.join(root_path ,"fixation.json")))
        logging.warning("The file " + root_path + "fixation.json already exists. Not regenerated")
        print("The file " + root_path + "fixation.json already exists. If you want to regenerate it, please remove it or change its name")
      else:
        fixation_p = gaze_log_mapping(ui_log, gazeanalysis_log, special_colnames, startDateTime_ui_log, startDateTime_gaze_tz, 'ms_webgazer')
        
      # Serializing json
      json_object = json.dumps(fixation_p, indent=4)
      with open(os.path.join(root_path ,"fixation.json"), "w") as outfile:
          outfile.write(json_object)
      logging.info("behaviourmonitoring/monitoring/monitoring. fixation.json saved!")
        
      fixation_json_to_dataframe(ui_log, fixation_p, special_colnames, root_path)
        
      monitoring_obj.executed = 100
      monitoring_obj.ub_log_path =os.path.join( root_path , "fixation.json")
      # update monitoring_obj
      monitoring_obj.save()
    
    elif monitoring_type == "tobii":
      #### Preprocessing tobii_gazelog Start ####
      pixels_threshold_i_dt = get_distance_threshold_by_resolution(monitoring_screen_inches,INCH_PER_CENTIMETRES, monitoring_observer_camera_distance,monitoring_screen_width,monitoring_screen_height) #Capturing the distance threshold in pixels regarding to the screen resolution
      minimum_fixation_gazepoints = get_minimum_fixation_gazepoints(DEVICE_FREQUENCY_TOBII, FIXATION_MINIMUM_DURATION) #Capturing the minimum number of gazepoints to consider a fixation

      if eyetracking_log_filename and os.path.exists(os.path.join(root_path , eyetracking_log_filename)):
          postprocessed_tobii_log = read_ui_log_as_dataframe(os.path.join(root_path , eyetracking_log_filename))
      else:
          logging.exception("behaviourmonitoring/monitoring/monitoring line:180. NOT PREPROCESSED Eyetracking  tobii log  cannot be read: " + root_path + eyetracking_log_filename)
          raise Exception("Eyetracking log cannot be read: " + root_path + eyetracking_log_filename)
      postprocessed_tobii_fixations_log_build = preprocess_gaze_log(postprocessed_tobii_log, "Gaze_X", "Gaze_Y",minimum_fixation_gazepoints,pixels_threshold_i_dt)# First Preprocesing of the tobii Log. In this Log, we get the fixations (Timestamps, duration, start,end, x,y, etc.)
      postprocessed_tobii_fixations_saccade_log_build = add_saccade_index(postprocessed_tobii_fixations_log_build)# Second Preprocesing of the tobii Log. In this Log, we get the saccades (Timestamps, duration, start,end, x,y, etc.)
      postprocessed_tobii_fixations_saccade_index_log_build = int_index(postprocessed_tobii_fixations_saccade_log_build)#Last Preprocesing of the tobii Log. In this Log, we get the index of the fixations and saccades to finally get the gazeanalysis Log (Idem to imotions log)
      postprocessed_tobii_fixations_saccade_index_log_build.to_csv(os.path.join(root_path , "tobii_gazedata_postprocessed.csv"))
      ##### Preprocessing tobii Log End #####
      if eyetracking_log_filename and os.path.exists(os.path.join(root_path ,"tobii_gazedata_postprocessed.csv")):
          gazeanalysis_log = read_ui_log_as_dataframe(os.path.join(root_path , "tobii_gazedata_postprocessed.csv"))
      else:
          logging.exception("behaviourmonitoring/monitoring/monitoring line:180. PREPROCESSED Eyetracking  tobii log cannot be read: " + root_path + "tobii_gazedata_preprocessed.csv")
          raise Exception("Eyetracking log cannot be read: " + root_path + eyetracking_log_filename)
        # fixation.json to Dataframe checker
      # fixation.json to Dataframe checker        
      for col_name in MONITORING_IMOTIONS_NEEDED_COLUMNS:
        if special_colnames[col_name] not in ui_log.columns:
          logging.error("Your UI log doesn't have a column representing : " + col_name + ". It must store information about " + str(MONITORING_IMOTIONS_NEEDED_COLUMNS))
          raise Exception("Your UI log doesn't have a column representing : " + col_name + ". It must store information about " + str(MONITORING_IMOTIONS_NEEDED_COLUMNS))
      
      startDateTime_gaze_tz = decode_timezone(root_path,monitoring_native_slide_events)#timezone y startslideeventdatetime
      
      #If the ui log file is a mht file. We need to get the startdatetime from the mht file with the get_mht_log_start_datetime function
      if ui_log_filename.endswith('.mht'):
        startDateTime_ui_log = get_mht_log_start_datetime(os.path.join(root_path , ui_log_filename), ui_log_format_pattern)
      #If the ui log file is a csv file. We need to get the startdatetime from the csv file with the get_csv_log_start_datetime function
      elif ui_log_filename.endswith('.csv'):
        startDateTime_ui_log = get_csv_log_start_datetime(os.path.join(root_path , ui_log_filename), ui_log_format_pattern)
        ui_log = convert_timestamps_and_clean_screenshot_name_in_csv(log_path)
        
        
        
      if os.path.exists(os.path.join(root_path ,"fixation.json")):
        fixation_p = json.load(open(os.path.join(root_path ,"fixation.json")))
        logging.warning("The file " + root_path + "fixation.json already exists. Not regenerated")
        print("The file " + root_path + "fixation.json already exists. If you want to regenerate it, please remove it or change its name")
      else:
        fixation_p = gaze_log_mapping(ui_log, gazeanalysis_log, special_colnames, startDateTime_ui_log, startDateTime_gaze_tz, 'ms_webgazer')
        
      # Serializing json
      json_object = json.dumps(fixation_p, indent=4)
      with open(os.path.join(root_path ,"fixation.json"), "w") as outfile:
          outfile.write(json_object)
      logging.info("behaviourmonitoring/monitoring/monitoring. fixation.json saved!")
        
      fixation_json_to_dataframe(ui_log, fixation_p, special_colnames, root_path)
        
      monitoring_obj.executed = 100
      monitoring_obj.ub_log_path =os.path.join( root_path , "fixation.json")
      # update monitoring_obj
      monitoring_obj.save()
 
    elif monitoring_type == "already_processed":
      print("Log processing: The log have been already processed")
    else:
      logging.exception("behaviourmonitoring/monitoring/monitoring line:195. Gaze analysis selected is not available in the system")
      raise Exception("You select a gaze analysis that is not available in the system")
        
    return os.path.join(root_path , "fixation.json")