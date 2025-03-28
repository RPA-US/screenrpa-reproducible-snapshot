import random
import pandas as pd
import math
import ctypes
import numpy as np
from scipy.spatial.distance import pdist

################# Constants #################
INCH_PER_CENTIMETRES = 2.54 #1 inch =  2.54cm

################# Constans to introduce by the user #################
SCREEN_INCHES = 21.5 #Screen Inches
OBSERVER_CAMERA_DISTANCE = 50 #cm 

################# Auxiliars Functions #################

def get_distance_threshold_by_resolution(screen_inches, inch_per_centimetres, observer_camera_distance, width, height):
    # user32 = ctypes.windll.user32
    # user32.SetProcessDPIAware()
    # width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1) #Screen Resolution
    
    angle_radians = np.radians(1)
    sin_value = np.sin(angle_radians)#Sin(1º) value
    print(f"sin(1º) = {sin_value}")
    
    radius_diameter = sin_value*observer_camera_distance*2 #Se multiplica por 2 para obtener el diámetro
    print(f"Fixation Boundary (diameter): {radius_diameter} cm.")

    screen_diagonal_pixels = math.sqrt((width)**2 + (height)**2)#diagonal de la pantalla en píxeles. Dependiendo de la resolución de la pantalla, tendrá un valor diferente
    print(f"Screen Diagonal Resolution (in pixels): {screen_diagonal_pixels} px.")
    
    pixels_per_inches = screen_diagonal_pixels/screen_inches
    print(f"Pixels per Inches: {pixels_per_inches} px/inches.")

    pixels_per_centimetres = pixels_per_inches/inch_per_centimetres
    print(f"Pixels per centimetres: {pixels_per_centimetres} px/centimetres.")

    pixels_threshold_i_dt = int(radius_diameter * pixels_per_centimetres)
    print(f"I-DT threshold (in pixels): {pixels_threshold_i_dt} px.")
    return pixels_threshold_i_dt

def get_minimum_fixation_gazepoints(device_frequency, fixation_minimum_duration):
    
    minimum_fixation_gazepoints = round(device_frequency*fixation_minimum_duration/1000)
    # print(f"The minimum gaze points considered to be a possible fixation is {minimum_fixation_gazepoints},\naccording to our device refresh rate and the established fixation minimum duration (200ms).\n")
    
    return minimum_fixation_gazepoints

def rescale_gaze_points(df, width, height):
    df = df.copy()
    if df["Screen_X"].iloc[0] != width or df["Screen_Y"].iloc[0] != height:
        df["Screen_Y"] = 737.60 #Portatil MSI pora tests
        # Reescalado de los gazepoints a la resolución configurada
        df['Gaze_X'] = df['Gaze_X'] * width / df['Screen_X']
        df['Gaze_Y'] = df['Gaze_Y'] * height / df['Screen_Y']
        # # Asegurar que los valores de Gaze_X y Gaze_Y estén dentro de los límites
        df['Gaze_X'] = df['Gaze_X'].apply(lambda x: width if x > width  else ( 0 if x < 0 else round(x,2)))
        df['Gaze_Y'] = df['Gaze_Y'].apply(lambda y: height  if y > height  else ( 0 if y < 0 else round(y,2)))
        # df["Gaze_Y"] = df['Gaze_Y'].apply(lambda y: y+50)
        # #40 px de la taskbar de Windows y 87 px de la barra de búsqueda de Chrome
        # df['Gaze_Y'] = df['Gaze_Y'].apply(lambda y: height  if y+127 > height  else ( 0 if y+87 < 0 else (y+87 if y < 0 else ( y+127 if y+127 < height else (round(y+87,2))))))
        
        

    return df   


################# I-DT Algorithm #################

def check_euclidean_distance(df, gaze_x_colname, gaze_y_colname, curr_df_index, cont, distance_threshold):
    gaze_x_ls = df[gaze_x_colname].iloc[curr_df_index - cont:curr_df_index + 1].tolist()
    gaze_y_ls = df[gaze_y_colname].iloc[curr_df_index - cont:curr_df_index + 1].tolist()

    max_gaze_x = max(gaze_x_ls)
    max_gaze_y = max(gaze_y_ls)
    min_gaze_x = min(gaze_x_ls)
    min_gaze_y = min(gaze_y_ls)

    distance = math.sqrt((max_gaze_x - min_gaze_x)**2 + (max_gaze_y - min_gaze_y)**2)

    return distance <= distance_threshold

def preprocess_gaze_log(df, gaze_x_colname, gaze_y_colname, min_points, distance_threshold):
    df = df.copy()
    df = df.rename_axis('RowNumber').reset_index()
    cont = 0 # curr_fixation_points que estan en el cluster que estoy estudiando
    pos_start_last_fixation = 0 # la posicion (row del df) del primer punto del fixation cluster
    pos_current_fixation = None
    fixation_index = 0 # el id que le voy a dar a este fixation cluster
    store_fixation = False
    for row_i, row in df.iterrows():
        aux = check_euclidean_distance(df, gaze_x_colname, gaze_y_colname, row_i, cont, distance_threshold)
        if aux:
            cont+=1 # hay un punto mas en el cluster
            centroid_x = df[gaze_x_colname].iloc[row_i - cont:row_i + 1].mean()
            centroid_y = df[gaze_y_colname].iloc[row_i - cont:row_i + 1].mean()
            
        else:
            cont=0 # se produce un saccade
        
        if cont >= min_points: 
            print("cont", cont)
            print("row_i", row_i)
            print("pos_current_fixation", pos_current_fixation)
            cond = pos_current_fixation and pos_current_fixation != row_i - 1 
            print("pos_current_fixation and pos_current_fixation != row_i - 1", cond)
            print("================================================================================")
            if cond: #Si es una fijación nueva... almaceno la posicion del cluster anterior
                pos_start_last_fixation_to_store = pos_start_last_fixation # se guarda la pos_start de la fijación
                pos_end_last_fixation = pos_current_fixation # la posición actual
                fixation_index +=1
                aux_pos = pos_start_last_fixation_to_store-1
                if pos_start_last_fixation_to_store-1 < 0:
                    aux_pos = 0
                    # last_fixation_start = df['Timestamp'].iloc[aux_pos:pos_start_last_fixation_to_store+1].mean()
                    # last_fixation_end = df['Timestamp'].iloc[pos_end_last_fixation:pos_end_last_fixation+2].mean()
                    # last_fixation_duration = last_fixation_end - last_fixation_start
                store_fixation = True
            pos_start_last_fixation = (row_i+1) - cont  #primer gaze_point del cluster
            pos_current_fixation = row_i
            
        if store_fixation:            
            if cont > 0:
                # Calcular el valor medio o centroide de 'gaze_x_colname' y 'gaze_y_colname' para el cluster de fijación actual
                centroid_x = df[gaze_x_colname].iloc[pos_start_last_fixation_to_store:pos_end_last_fixation + 1].mean()
                centroid_y = df[gaze_y_colname].iloc[pos_start_last_fixation_to_store:pos_end_last_fixation + 1].mean()
                
                # Calcular el comienzo de una fijación (tiempo medio entre el ultimo punto que es una sacada 
                # y el primer punto que es una fijación) 
                last_fixation_start = df['Timestamp'].iloc[aux_pos:pos_start_last_fixation_to_store+1].mean()
                # y el final de una fijación (tiempo medio entre el ultimo punto que es una fijación 
                # y el primer punto que es una sacada)
                last_fixation_end = df['Timestamp'].iloc[pos_end_last_fixation:pos_end_last_fixation+2].mean()
                # y la duración de la fijación (tiempo entre el comienzo y el final de la fijación)
                last_fixation_duration = last_fixation_end - last_fixation_start
                
                

            else:
                #Reseteamos
                centroid_x = 0
                centroid_y = 0


            # añadir el fixation index desde la row pos_start_last_fixation hasta el pos_current_fixation en la columna de fixation_index del df
            df.loc[pos_start_last_fixation_to_store:pos_end_last_fixation, 'Fixation Index'] = fixation_index
            # Almacenar el índice de fijación y el valor medio en nuevas columnas
            df.loc[pos_start_last_fixation_to_store:pos_end_last_fixation, 'Fixation X'] = centroid_x
            df.loc[pos_start_last_fixation_to_store:pos_end_last_fixation, 'Fixation Y'] = centroid_y
            df.loc[pos_start_last_fixation_to_store:pos_end_last_fixation, 'Fixation Start'] = last_fixation_start
            df.loc[pos_start_last_fixation_to_store:pos_end_last_fixation, 'Fixation End'] = last_fixation_end
            df.loc[pos_start_last_fixation_to_store:pos_end_last_fixation, 'Fixation Duration'] = last_fixation_duration
            # Indice de dispersión: Puede calcularse como la relación entre la media de las distancias entre todos los puntos 
            # y la distancia media desde cada punto hasta el centro del conjunto. 
            # Un índice mayor indica mayor dispersión.

            
    
            
            # print("estoy guardando los fixation")
            # print("fixation_index: ", fixation_index)
            # print("pos_start_last_fixation: ", pos_start_last_fixation)
            # print("pos_end_last_fixation: ", pos_end_last_fixation)
            # print("cont: ", cont)
            store_fixation = False
            
    return df


def add_saccade_index(df):
    df = df.copy()
    df['Saccade Index'] = None
    saccade_index = 1
    for row_i, row in df.iterrows():
        if pd.isnull(row['Fixation Index']):
            df.loc[row_i, 'Saccade Index'] = saccade_index
            saccade_index += 1
    return df


def int_index(df):
    df = df.copy()
    df['Saccade Index'] = df['Saccade Index'].astype('Int64')
    df['Fixation Index'] = df['Fixation Index'].astype('Int64')
    return df
