import os
import json
import pickle
import numpy as np
import pandas as pd
import time
from collections import Counter
import scipy
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split

#from apps.decisiondiscovery.decision_trees import update_json_with_rules
from .utils import def_preprocessor, best_model_grid_search, cross_validation, extract_tree_rules, prev_preprocessor, rename_columns_with_centroids

# Buscar en el paths_dict aquellas reglas que se repiten en distintas claves, y devolver una lista con estas claves
def find_overlapping_rules(paths_dict):
    overlapping_rules = []
    for key in paths_dict.keys():
        for key2 in paths_dict.keys():
            if key != key2:
                for rule in paths_dict[key]:
                    if rule in paths_dict[key2]:
                        overlapping_rules.append((key, key2))
    return overlapping_rules


def check_dict_structure(paths_dict, dict_key):
    if dict_key not in paths_dict['paths']:
        paths_dict['paths'][dict_key] = {'rules': [], 'overlapping_rules': []}
    else:
        if 'overlapping_rules' not in paths_dict['paths'][dict_key] :
            paths_dict['paths'][dict_key]['overlapping_rules'] = []
    return paths_dict

def extract_paths(tree, feature_names):
    paths = {'paths': {}, 'metadata': {}}
    n_nodes = tree.tree_.node_count
    children_left = tree.tree_.children_left
    children_right = tree.tree_.children_right
    feature = tree.tree_.feature
    threshold = tree.tree_.threshold
    value = tree.tree_.value

    # Función para recorrer el árbol
    def recurse(node, path):
        if children_left[node] != children_right[node]:
            # Si no es una hoja, sigue recorriendo
            left_path = path + [f"{feature_names[feature[node]]} <= {threshold[node]:.2f}"]
            right_path = path + [f"{feature_names[feature[node]]} > {threshold[node]:.2f}"]
            recurse(children_left[node], left_path)
            recurse(children_right[node], right_path)
        else:
            # Es una hoja, determina la clase y guarda el camino
            class_index = np.argmax(value[node])
            class_label = tree.classes_[class_index]
            if class_label not in paths['paths']:
                paths['paths'][class_label] = {'rules': [], 'overlapping_rules': []}
            paths['paths'][class_label]['rules'].append(" & ".join(path))

    # Inicia el recorrido desde la raíz
    recurse(0, [])

    # Imprime los caminos para cada clase
    # for label, paths_aux in paths['paths'].items():
    #     print(f"Clase {label}:")
    #     for path in paths_aux['rules']:
    #         print(f"  - {path}")
            
    return paths


# Función para obtener las reglas del árbol de decisión
def get_decision_path(tree, X_sample):
    feature_names = X_sample.columns
    node_indicator = tree.decision_path(X_sample)
    leaf_id = tree.apply(X_sample)
    paths = []
    for sample_id, node_index in enumerate(leaf_id):
        path_nodes = node_indicator.indices[node_indicator.indptr[sample_id]:node_indicator.indptr[sample_id + 1]]
        path = []
        for node_id in path_nodes:
            if tree.tree_.children_left[node_id] == tree.tree_.children_right[node_id]:  # Es una hoja
                path.append(f"class: {tree.classes_[np.argmax(tree.tree_.value[node_id])]}")
                break
            else:
                if X_sample.iloc[sample_id, tree.tree_.feature[node_id]] <= tree.tree_.threshold[node_id]:
                    path.append(f"{feature_names[tree.tree_.feature[node_id]]} <= {tree.tree_.threshold[node_id]:.2f}")
                else:
                    path.append(f"{feature_names[tree.tree_.feature[node_id]]} > {tree.tree_.threshold[node_id]:.2f}")
        # drop the last item from paths because it's the class
        # path_class = path.pop(-1)
        path.pop(-1)
        # paths.append("(" + " and ".join(path) + ") -> " + path_class)
        
        paths.append(" & ".join(path))
    return paths

# Función para actualizar traceability.json con las reglas de paths_dict

def update_json_with_rules(traceability_json, path_dict):
    for decision_point in traceability_json["decision_points"]:
        for branch in decision_point["branches"]:
            label = branch["label"]
            if label + ".0" in path_dict["paths"]:
                decision_point["rules"][label] = path_dict["paths"][label + ".0"]["rules"]
                if "overlapping_rules" not in decision_point:
                    decision_point["overlapping_rules"] = {}
                decision_point["overlapping_rules"][label] = path_dict["paths"][label + ".0"]["overlapping_rules"]
    return traceability_json   

#df,prevact, param_path, special_colnames, configuration, one_hot_columns, target_label, k_fold_cross_validation
def overlapping_rules(df,prevact, param_path, special_colnames, configuration, one_hot_columns, target_label, k_fold_cross_validation):
    times = {}
    accuracies = {}
    
    #columna_objetivo = 'variant'
    min_samples_split = 1
    merge_ratio_e = 0.5
    
    # # Split data into training and testing sets
    
    # X = df.drop(columna_objetivo, axis=1)
    # y = df[columna_objetivo]
    
    # # Preprocess data
    # preprocessor = def_preprocessor(X)
    # X = preprocessor.fit_transform(X)
    
    # # Save preprocessed data
    # X_df = pd.DataFrame(X)
    # feature_names = list(preprocessor.get_feature_names_out())
    # X_df.to_csv(os.path.join(param_path, "preprocessed_df.csv"), header=feature_names)

    if "e50_" in param_path:
        k_fold_cross_validation = 2
    ###########################################################################
    # Extract features and target variable
    df = df.dropna(subset=['dp_branch'])
    X = df.drop(columns=[special_colnames["Variant"]]).drop(columns=["dp_branch"])
    #X = X.astype(str)
    y = df["dp_branch"]

    y = y.astype(str)

    X = prev_preprocessor(X)
    if isinstance(X, str):
        raise Exception(X)
        return
    preprocessor = def_preprocessor(X)
    #df.head()
    try:
        X = preprocessor.fit_transform(X)
    except Exception as e:
        raise Exception(e)
        return
    # X es una sparse_matrix
    # if a dataframe has a high number of columns, may be it has to be treated as a sparse matrix
    if isinstance(X, scipy.sparse.spmatrix):
        X = X.toarray()
    X_df = pd.DataFrame(X, columns=preprocessor.get_feature_names_out())
    postprocessor= X_df.columns[X_df.nunique() == 1].tolist()
    if len(postprocessor) != 0:
        X_df= X_df.drop(columns=postprocessor)
    else:
        Exception("No features left after preprocessing.")
    
    X_df = rename_columns_with_centroids(X_df)
#    X_df=rename_nan_columns(X_df)
    feature_names = X_df.columns.tolist()


    X_df.to_csv(os.path.join(param_path, "preprocessed_df_"+prevact+".csv"), header=feature_names)
    ###############################################3
    
    print(y.unique())
    tree_classifier_1, accuracy, paths_dict,y_train = overlapping_rules_from_tree(param_path, X_df, y, min_samples_split, merge_ratio_e)   
    
    start_t = time.time()
    # Find the best model using grid search
    tree_classifier, best_params = best_model_grid_search(X_df, y, tree_classifier_1, k_fold_cross_validation)
    
    accuracies = cross_validation(X_df,pd.DataFrame(y),None,special_colnames['Variant'],"sklearn",tree_classifier,k_fold_cross_validation)
    
    times["sklearn"] = {"duration": float(time.time()) - float(start_t)}
    # Retrieve the decision tree rules
    text_representation = export_text(tree_classifier_1, feature_names=feature_names)
    print("Decision Tree Rules:\n", text_representation)
    
    
    with open(os.path.join(param_path, "decision_tree_"+prevact+".log"), "w") as fout:
        fout.write(text_representation)

    saved_data = {
        'classifier': tree_classifier_1,
        'feature_names': feature_names,
        'class_names': np.unique(y_train),
    }
    with open(os.path.join(param_path, 'decision_tree_'+prevact+'.pkl'), 'wb') as fid:
        pickle.dump(saved_data, fid)

    traceability_path= (os.path.join(param_path, 'traceability.json'))

    if paths_dict is not None:
        with open(traceability_path, 'r') as f:
            json_data = json.load(f)
        
        updated_json = update_json_with_rules(json_data, paths_dict)
        
        with open(traceability_path, 'w') as f:
            json.dump(updated_json, f, indent=2)
    # Grid Search
    accuracies["selected_params"] = best_params

    return accuracies, times


def overlapping_rules_from_tree(
        param_path,
        X,
        y,
        min_samples_split,
        merge_ratio_e
    ):

    # Divide el dataset en conjuntos de entrenamiento y prueba
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=4)

    # Entrenar el modelo de árbol de decisión
    tree = DecisionTreeClassifier()  # Usando entropía para simular C4.5
    tree.fit(X_train, y_train)

    # Evaluar el modelo
    y_pred = tree.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    print(f'\n===================================\n Tree Accuracy: {accuracy * 100:.2f}% ')

    feature_names = X.columns.tolist()
    paths_dict = extract_paths(tree, feature_names)

    certain_labels = set(pd.unique(y_test))

    if accuracy == 1.0:
        tree_text = export_text(tree, feature_names=list(X.columns))
        with open(os.path.join(param_path, 'full_tree.txt'), 'w') as f:
            f.write(tree_text)
    else:
        tree_text = export_text(tree, feature_names=list(X.columns))
        with open(os.path.join(param_path, 'failed_tree.txt'), 'w') as f:
            f.write(tree_text)
        misclassified = y_test != y_pred
        misclassified_data = X_test[misclassified]
        misclassified_labels = y_test[misclassified]
        misclassified_preds = y_pred[misclassified]

        for label in pd.unique(misclassified_labels):
            subset = misclassified_data[misclassified_labels == label]
            subset_target = misclassified_labels[misclassified_labels == label]
            subset_preds = misclassified_preds[misclassified_labels == label]
            
            # Calcula la frecuencia del ítem con mayor frecuencia
            freqs = Counter(subset_preds)
            t_prima, max_freq = freqs.most_common(1)[0]
            freq_t_prima = max_freq / len(subset_preds)
            
            if subset.empty:
                continue
            
            # Verifica las condiciones y actualiza paths_dict
            if len(subset_preds) >= min_samples_split and freq_t_prima > merge_ratio_e:      
                # Obtener las reglas de decisión para las instancias misclassified
                subset_paths = get_decision_path(tree, subset)
            
                # Guarda el subset con predicciones, etiquetas reales y caminos de decisión
                subset['ground_truth'] = subset_target
                subset['prediction'] = subset_preds
                subset['decision_path'] = subset_paths
                #subset.to_csv(f'subset_for_class_{label}.csv', index=False)
                            
                t_prima_subset = subset[subset_preds == t_prima]
                first_occurence = t_prima_subset.iloc[0]
        
                paths_dict = check_dict_structure(paths_dict, first_occurence['ground_truth'])
                paths_dict['paths'][first_occurence['ground_truth']]['overlapping_rules'].append(first_occurence['decision_path'])
                
                paths_dict = check_dict_structure(paths_dict, first_occurence['prediction'])
                                # Verifica si la regla existe antes de intentar eliminarla
                if first_occurence['decision_path'] in paths_dict['paths'][first_occurence['prediction']]['rules']:
                    paths_dict['paths'][first_occurence['prediction']]['rules'].remove(first_occurence['decision_path'])
                    paths_dict['paths'][first_occurence['prediction']]['overlapping_rules'].append(first_occurence['decision_path'])
               
                # obtener como una lista de tuplas, los pares distintos de valores que existen entre la columna ground_truth y prediction
                x_missclassified_as_y = subset[['ground_truth', 'prediction']].drop_duplicates().values.tolist()
                
                for aux_pair in x_missclassified_as_y:
                    for un_label in aux_pair:
                        if un_label in certain_labels:
                            certain_labels.remove(un_label)
                
                
                # Entrena un nuevo árbol con el subset
                sub_tree = DecisionTreeClassifier(criterion='entropy')
                sub_tree.fit(subset.drop(['prediction', 'ground_truth', 'decision_path'], axis=1), subset_target)
                sub_tree_text = export_text(sub_tree, feature_names=list(subset.columns[:-3]))
                
                paths_dict['metadata'] = {  
                    'guard': first_occurence['decision_path'],
                    'x_missclassified_as_y': x_missclassified_as_y,
                    'subset_indexes': t_prima_subset.index.tolist(),
                    'subset_size': len(subset_preds),
                    'fraction_over_subset': freq_t_prima
                }
                aux_path = os.path.join(param_path, 'tree_for_class_' + label + '.txt')
                with open(aux_path, 'w') as f:
                    f.write(sub_tree_text)
                

    print(f"\n\n===================================\nVariants with 100% certainty:\n===================================")
    print(f"  Classes whose guards have a 100% certainty: {certain_labels}")
    # Imprimir las rules asociadas a las clases que tienen una certeza del 100%
    for label in certain_labels:
        print(f"  Class {label}:")
        if label in paths_dict['paths']:
            for rule in paths_dict['paths'][label]['rules']:
                print(rule)
        else:
            print("  No rules found for this class.")


    print(f"\n\n===================================\nVariants with uncertainty:\n===================================")
    if 'metadata' in paths_dict:
        # Comprueba que exista la clave 'x_missclassified_ñas_y' en paths_dict['metadata']
        if 'x_missclassified_as_y' in paths_dict['metadata']:
            for confusion in paths_dict['metadata']['x_missclassified_as_y']:
                print(f"  Overlapping rules: the class {confusion[0]} (ground_truth) is missclassified as the class {confusion[1]} (prediction)\n")
                # Imprimir las rules de las claves en 'paths' en paths_dict que estan contenidas en confusion
                for key in confusion:

                    if key in paths_dict['paths']:
                        print(f"  Class {key}:")
                        for rule in paths_dict['paths'][key]['rules']:
                                print(f"    - (precision 100%)  {rule}")
                            
                        if 'overlapping_rules' in paths_dict['paths'][key]:
                            for rule in paths_dict['paths'][key]['overlapping_rules']:
                                print(f"    - (overlapping)      {rule}")
        
        
        
            
    # Guarda las reglas de decisión en un archivo JSON
    with open(os.path.join(param_path, 'paths.json'), 'w') as f:
        json.dump(paths_dict, f, indent=4)
    
    return tree, accuracy, paths_dict, y_train


# from sklearn.metrics import confusion_matrix
# import seaborn as sns
# import matplotlib.pyplot as plt

# # Generar la matriz de confusión
# conf_matrix = confusion_matrix(y_test, y_pred)

# print("y_test")
# print(y_test)
# print("y_pred")
# print(y_pred)

# # Visualizar la matriz de confusión
# sns.heatmap(conf_matrix, annot=True, fmt='g')
# plt.xlabel('Predicted labels')
# plt.ylabel('True labels')
# plt.title('Confusion Matrix')
# plt.show()