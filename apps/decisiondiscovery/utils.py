import pickle
import re
import math
import numpy as np
import pandas as pd

from typing import List, Union, Dict, Any, Tuple

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from sklearn.metrics import accuracy_score
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import (
    OneHotEncoder,
    OrdinalEncoder,
)  # LabelEncoder, StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.model_selection import StratifiedKFold, GridSearchCV
from sklearn.metrics import f1_score, accuracy_score, precision_score, recall_score
from apps.chefboost import Chefboost as chef
from .models import ExtractTrainingDataset, DecisionTreeTraining
import json
from sklearn.tree import _tree
###########################################################################################################################
# case study get phases data  ###########################################################################################
###########################################################################################################################


def get_extract_training_dataset(case_study):
    return get_object_or_404(ExtractTrainingDataset, case_study=case_study, active=True)


def get_decision_tree_training(case_study):
    return get_object_or_404(DecisionTreeTraining, case_study=case_study, active=True)


def case_study_has_extract_training_dataset(case_study):
    return ExtractTrainingDataset.objects.filter(
        case_study=case_study, active=True
    ).exists()


def case_study_has_decision_tree_training(case_study):
    return DecisionTreeTraining.objects.filter(
        case_study=case_study, active=True
    ).exists()


###########################################################################################################################


def best_model_grid_search(X_train, y_train, tree_classifier, k_fold_cross_validation):
    # Define the hyperparameter grid for tuning
    param_grid = {
        "criterion": ["gini", "entropy"],
        "splitter": ["best", "random"],
        "max_depth": [None, 5, 10, 15],
        "min_samples_split": [2, 5, 10],
        "min_samples_leaf": [1, 2, 5],
    }

    # Perform GridSearchCV to find the best hyperparameters
    grid_search = GridSearchCV(
        estimator=tree_classifier, param_grid=param_grid, cv=k_fold_cross_validation
    )
    grid_search.fit(X_train, y_train)

    # Get the best hyperparameters and train the final model
    best_tree_classifier = grid_search.best_estimator_
    print("Grid Search Best Params:\n", grid_search.best_params_)

    best_tree_classifier.fit(X_train, y_train)

    return best_tree_classifier, grid_search.best_params_


def cross_validation(
    X, y, config, target_label, library, model, k_fold_cross_validation
):
    # Cross-validation: accurracy + f1 score
    accuracies = {}

    min_representation = min(y.value_counts())
    if min_representation < k_fold_cross_validation:
        k_fold_cross_validation = min_representation
    skf = StratifiedKFold(n_splits=k_fold_cross_validation)
    # skf.get_n_splits(X, y)

    metrics_acc = []
    metrics_precision = []
    metrics_recall = []
    metrics_f1 = []

    for i, (train_index, test_index) in enumerate(skf.split(X, y)):
        print("Fold {}:".format(i))
        print("Train: index={}".format(train_index))
        print("Test:  index={}".format(test_index))
        X_train_fold, X_test_fold = X.iloc[train_index], X.iloc[test_index]
        y_train_fold, y_test_fold = y.iloc[train_index], y.iloc[test_index]

        if library == "chefboost":
            df_train_fold = pd.concat([X_train_fold, y_train_fold], axis=1)
            current_iteration_model, acc = chef.fit(df_train_fold, config, target_label)
        elif library == "sklearn":
            current_iteration_model = model.fit(X_train_fold, y_train_fold)
        else:
            raise Exception("Decision Model Option Not Valid")

        if library == "chefboost":
            y_pred = []
            for _, X_test_instance in X_test_fold.iterrows():
                y_pred.append(chef.predict(current_iteration_model, X_test_instance))
        elif library == "sklearn":
            y_pred = current_iteration_model.predict(X_test_fold)
        else:
            raise Exception("Decision Model Option Not Valid")

        metrics_acc.append(accuracy_score(y_test_fold, y_pred))
        metrics_precision.append(
            precision_score(y_test_fold, y_pred, average="weighted")
        )
        metrics_recall.append(recall_score(y_test_fold, y_pred, average="weighted"))
        metrics_f1.append(f1_score(y_test_fold, y_pred, average="weighted"))

    accuracies["accuracy"] = np.mean(metrics_acc)
    accuracies["precision"] = np.mean(metrics_precision)
    accuracies["recall"] = np.mean(metrics_recall)
    accuracies["f1_score"] = np.mean(metrics_f1)
    print(
        "Stratified K-Fold:  accuracy={} f1_score={}".format(
            accuracies["accuracy"], accuracies["f1_score"]
        )
    )
    return accuracies, current_iteration_model


def preprocess_data(data):
    columns_to_drop = list(filter(lambda x: "TextInput" in x, data.columns))
    data = data.drop(columns=columns_to_drop)
    return data


def prev_preprocessor(X):
    # define type of columns
    # sta_columns = list(filter(lambda x:"sta_" in x, X.columns))

    X = X.loc[
        :, ~X.columns.str.contains("^Unnamed")
    ]  # Remove unnamed columns automatically generated
    # Identificar las columnas con todos los valores nulos
    columns_to_drop = X.columns[X.isnull().all()].tolist()
    # Eliminar las columnas con todos los valores iguales o nulos
    X_drop = X.drop(columns=columns_to_drop)

    if len(X_drop.columns) == 0:
        return "No features left after preprocessing."

    return X_drop


def def_preprocessor(X):
    # Define el diccionario de mapeo para las columnas "enabled" y "checked"
    mapping_dict = {
        "enabled": ["NaN", "enabled", "disabled"],
        "checked": ["unchecked", "checked", ""],
    }

    mapping_list = []
    sta_columns = []

    # Identificar las columnas que contienen "rpa-us_" en su nombre
    for col in X.columns:
        if "rpa-us_" in col:
            # sta_columns.append(col)
            if "enabled" in col:
                mapping_list.append(mapping_dict["enabled"])
            elif "checked" in col:
                mapping_list.append(mapping_dict["checked"])
            else:
                # Si la columna no coincide con ninguna categoría conocida, agregar un mapeo genérico
                unique_values = X[col].dropna().unique().tolist()
                if "NaN" not in unique_values:
                    unique_values.append("NaN")
                mapping_list.append(unique_values)

    # Identificar columnas de tipo objeto y numéricas
    types_obj = X.select_dtypes(include=["object"]).columns
    # Convertir estas columnas a string
    for obj_col in types_obj:
        X[obj_col] = X[obj_col].astype(str)
    one_hot_columns = list(types_obj.drop(sta_columns, errors="ignore"))
    numeric_features = X.select_dtypes(include=["number"]).columns

    # Crear cada transformador
    # status_transformer = Pipeline(steps=[
    #     ('imputer', SimpleImputer(strategy='constant', fill_value='NaN')),
    #     ('label_encoder', OrdinalEncoder(categories=mapping_list))
    # ])

    one_hot_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="constant", fill_value="NaN")),
            ("one_hot_encoder", OneHotEncoder()),
        ]
    )

    numeric_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="mean")),
            # ('scaler',StandardScaler()) # Descomentar si se requiere escalado
        ]
    )

    # Crear el preprocesador
    preprocessor = ColumnTransformer(
        transformers=[
            ("numeric", numeric_transformer, numeric_features),
            ("one_hot_categorical", one_hot_transformer, one_hot_columns),
        ]
    )

    return preprocessor


def create_and_fit_pipeline(X, y, model):
    preprocessor = def_preprocessor(X, [])
    # create pipeline
    pipeline = Pipeline(steps=[("preprocessor", preprocessor), ("model", model)])

    # fit pipeline
    pipeline.fit(X, y)

    return pipeline


# def rename_nan_columns(df):
#     for col in df.columns:
#         if df[col].nunique() == 2 and 'nan' in col.lower():
#             # Obtener los nombres únicos en la columna, excluyendo 'NaN'
#             unique_values = df[col].unique().tolist()
#             non_nan_value = [val for val in unique_values if 'nan' not in str(val).lower()][0]
#             # Renombrar la columna
#             df.rename(columns={col: non_nan_value}, inplace=True)
#     return df


# Formating textual representation of decision trees
def parse_decision_tree(file_path):
    tree_structure = []

    with open(file_path, "r") as file:
        lines = file.readlines()

    def parse_node(node_str, depth):
        # Numeric condition
        match = re.match(
            r"\|   " * (get_node_depth(node_str) - 1)
            + r"\|--- *(.+) *(<=|>=|>|<)\s+([0-9.-]*)",
            node_str,
        )
        if match:
            feature, operator, threshold = match.groups()
            return [feature.strip(), operator.strip(), float(threshold.strip())]

        # String condition
        match = re.match(
            r"\|   " * (get_node_depth(node_str) - 1)
            + r"\|--- *(.+) *(==|!=)\s+([0-9a-zA-Z.-]*)",
            node_str,
        )
        if match:
            feature, operator, threshold = match.groups()
            return [feature.strip(), operator.strip(), threshold]

        # Branch resolve
        match = re.match(
            r"\|   " * (get_node_depth(node_str) - 1) + r"\|--- class: (.+)", node_str
        )
        if match:
            class_value = match.group(1)
            return f"class: {class_value}"

    def get_node_depth(node_str):
        return len(re.findall(r"\|", node_str))

    def build_tree(lines, index, depth, max_depth):
        if index < 0:
            node_depth = 0
            node = ["root", "None", "None"]
        else:
            node_str = lines[index].strip()
            node_depth = get_node_depth(node_str)
            node = parse_node(node_str, node_depth)

        next_index = index + 1
        if node_depth == depth:
            if isinstance(node, list):
                children = []
                while next_index < len(lines):
                    child_depth = get_node_depth(lines[next_index].strip())
                    max_depth = child_depth if child_depth > max_depth else max_depth

                    if child_depth > node_depth:
                        child, max_depth, next_index = build_tree(
                            lines, next_index, child_depth, max_depth
                        )
                        children.append(child)
                    else:
                        break
                node.append(children)
                return node, max_depth, next_index
            else:
                return node, max_depth, next_index
        else:
            return node, max_depth, index

    tree_structure, max_depth, index = build_tree(lines, -1, depth=0, max_depth=0)

    return tree_structure, max_depth


# Check path inside decision tree representation
def points_distance(punto_x, punto_y):
    x1, y1 = punto_x
    x2, y2 = punto_y
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def centroid_distance_checker(punto_x, punto_y, umbral):  # -> Literal[True]:
    if not punto_x and not punto_y:
        return True
    else:
        distancia = points_distance(punto_x, punto_y)
        return distancia <= umbral


# def read_feature_column_name(column_name):

#     # Buscar el patrón en la cadena de texto
#     contains_centroid = bool(re.search(r'\d+\.\d+-\d+\.\d+', column_name))

#     # Definimos la expresión regular para buscar los componentes del identificador
#     if "__" in column_name and contains_centroid:
#         pattern = r"(.*)__([a-zA-Z]+_[a-zA-Z]+)_(\d+\.\d+-\d+\.\d+)_(\d*_?[a-zA-Z])"
#         aux1 = 1
#         aux2 = 1
#     elif "__" in column_name and not contains_centroid:
#         pattern = r"(\w+)__(\w+)_(\w+_\w+)"
#         centroid = None
#         aux1 = 1
#         aux2 = 0
#     elif not "__" in column_name and not contains_centroid:
#         pattern = r"(\w+)_(\w+_\w+)"
#         suffix = None
#         aux1 = 0
#         centroid = None
#         aux2 = 0
#     elif not "__" in column_name and not contains_centroid: #one_hot_categorical__case:concept:name_1_1
#         pattern = r"([a-zA-Z_]+)__([a-zA-Z:+]+)_(\d+_\d+)"
#         suffix = None
#         aux1 = 0
#         aux2 = 1
#     elif "__" in column_name and contains_centroid: #status_categorical__rpa-us_282.6106567382812-167.1426658630371_1
#         pattern = r"([a-zA-Z_]+)__([a-zA-Z-]+)_(\d+\.\d+-\d+\.\d+)_(\d+)"
#         suffix = None
#         aux1 = 0
#         aux2 = 1
#     else:
#         pattern = r"([a-zA-Z]+_[a-zA-Z]+)_(\d+\.\d+-\d+\.\d+)_(\d*_?[a-zA-Z])"
#         suffix = None
#         aux1 = 0
#         aux2 = 1

#     # Buscamos las coincidences en el identificador utilizando la expresión regular
#     coincidences = re.match(pattern, column_name)

#     if coincidences:
#         if aux1 == 1:
#             suffix = coincidences.group(1)
#         feature = coincidences.group(aux1+1)
#         if aux2 == 1:
#             centroid = [float(coincidences.group(aux1+2).split("-")[0]), float(coincidences.group(aux1+2).split("-")[1])]
#         activity = coincidences.group(aux1+aux2+2)
#     else:
#         raise Exception(_("The identifier does not follow the pattern"))

#     return suffix, feature, centroid, activity


# def read_feature_column_name(column_name):
#     contains_centroid = bool(re.search(r'\d+\.\d+-\d+\.\d+', column_name))

#     if contains_centroid:
#         pattern = r"([a-zA-Z_]+)__([a-zA-Z-]+)_(\d+\.\d+-\d+\.\d+)_(\d+)(_?[a-zA-Z]?)"
#     else:
#         #pattern = r"([a-zA-Z_]+)__([a-zA-Z_]+)_(\d+)"
#         pattern = r"([a-zA-Z_]+)__([a-zA-Z0-9_]+)_(\d+)(_?[a-zA-Z]?)"

#     # Intentamos encontrar coincidencias con el patrón definido
#     coincidences = re.match(pattern, column_name)

#     # Verificamos si hay coincidencias antes de intentar acceder a los grupos
#     if not coincidences:
#         raise Exception(f"The identifier '{column_name}' does not follow the pattern")

#     suffix = coincidences.group(1)
#     feature = coincidences.group(2)
#     if contains_centroid:
#         centroid = [float(coord) for coord in coincidences.group(3).split("-")]
#         activity = coincidences.group(4)
#     else:
#         centroid = None
#         activity = coincidences.group(3)

#     return suffix, feature, centroid, activity
#     # Si no coincide con ninguno de los patrones


def read_feature_column_name(column_name):
    # Patrón para los nombres de columna que contienen centroid
    pattern_with_centroid = r"([a-zA-Z0-9_]+__)?([a-zA-Z0-9_-]+)_(\d+\.?\d*?-\d+\.?\d*?)_(\d+)(_?)([_0-9a-zA-Z]+)"
    pattern_with_sta_centroid = r"([a-zA-Z0-9_]+__)?([a-zA-Z0-9_-]+)_(\d+\.?\d*?-\d+\.?\d*?)(_?)([_0-9a-zA-Z]+)_(\d+)"
    # Patrón para los nombres de columna que no contienen centroid
    pattern_without_centroid = r"([a-zA-Z0-9_]+__)?([a-zA-Z0-9_]+)_(\d+)(_?[a-zA-Z]?)"
    # Patrón adicional para nombres de columna sin prefijo
    pattern_no_prefix = r"([a-zA-Z0-9_]+)_(\d+\.\d+-\d+\.\d+)_(\d+)(_?[a-zA-Z]?)"
    # Patroón para puntos de decisión
    # one_hot_categorical__idc2257948-fb8b-4a60-8ea6-1fdce0c602a1_*
    pattern_decision_point = (
        r"([a-za-z_]+__)?([a-zA-Z0-9-]+)_([a-zA-Z]+)?_?([_a-zA-Z0-9-]+)"
    )

    # Intentamos encontrar coincidencias con los patrones definidos
    coincidences = re.match(pattern_with_centroid, column_name)
    if coincidences:
        suffix = coincidences.group(1) if coincidences.group(1) else None
        feature = coincidences.group(2)
        centroid = [float(coord) for coord in coincidences.group(3).split("-")]
        activity = coincidences.group(4)
        return suffix, feature, centroid, activity

    coincidences = re.match(pattern_with_sta_centroid, column_name)
    if coincidences:
        suffix = coincidences.group(1) if coincidences.group(1) else None
        feature = coincidences.group(2)
        centroid = [float(coord) for coord in coincidences.group(3).split("-")]
        activity = coincidences.group(4)
        return suffix, feature, centroid, activity

    coincidences = re.match(pattern_without_centroid, column_name)
    if coincidences:
        suffix = coincidences.group(1) if coincidences.group(1) else None
        feature = coincidences.group(2)
        centroid = None
        activity = coincidences.group(3)
        if coincidences.group(4):  # Si hay un grupo 4 adicional (opcional)
            activity += coincidences.group(4)
        return suffix, feature, centroid, activity

    coincidences = re.match(pattern_no_prefix, column_name)
    if coincidences:
        suffix = None
        feature = coincidences.group(1)
        centroid = [float(coord) for coord in coincidences.group(2).split("-")]
        activity = coincidences.group(3)
        if coincidences.group(4):  # Si hay un grupo 4 adicional (opcional)
            activity += coincidences.group(4)
        return suffix, feature, centroid, activity

    coincidences = re.match(pattern_decision_point, column_name)
    if coincidences:
        suffix = coincidences.group(1) if coincidences.group(1) else None
        feature = coincidences.group(2)
        centroid = None
        activity = coincidences.group(4)  # Actividad o numero de puerta xor
        if coincidences.group(
            3
        ):  # Si hay un grupo 3 adicional (opcional, significa xor)
            activity = f"{coincidences.group(3)}_{activity}"
        return suffix, feature, centroid, activity

    # Si no coincide con ninguno de los patrones
    raise Exception(f"The identifier '{column_name}' does not follow the pattern")


def find_paths_to_target_variable(
    tree: List,
) -> List[Dict[str, Union[List[List[str]], str]]]:
    """
    Recursively explores branches in the given tree structure and extracts all possible
    routes leading to the specified target.

    Args:
        tree (list): The tree structure as a nested list.
        target (str): The target class to extract paths for.
        path (list): The current path of conditions being evaluated (used internally).

    Returns:
        List[Dict[str, Union[List[List[str]], str]]]:
            A list of dictionaries, each containing the conditions and target variable.
    """

    def aux_find_paths_to_target_variable(
        tree: List, path: list[Any] = []
    ) -> List[Dict[str, Union[List[List[str]], str]]]:
        # Base case: If we encounter a leaf node, then we return the path
        result: List[Dict[str, List[Any]]] = []

        if not any(isinstance(item, list) for item in tree):
            result = [
                {"path": path, "target": tree.split(":")[-1].strip()}
            ]  # tree only represents the leaf, which is a string ej. 'class: 5'

        elif tree[0] != "root":
            condition: list[str | int] = tree[:3]
            branches: list[Any] = tree[3]
            for branch in branches:
                path_copy = path.copy()
                path_copy.append(condition)
                result.extend(aux_find_paths_to_target_variable(branch, path=path_copy))

        else:
            result = []
            branches: list[Any] = tree[3]
            for branch in branches:
                result.extend(aux_find_paths_to_target_variable(branch, path=path))

        return result

    return aux_find_paths_to_target_variable(tree, path=[])


def find_path_in_decision_tree(
    paths, feature_values, centroid_threshold=250
) -> Tuple[bool, Dict[str, Any]]:
    """
    This function checks if the given path is compliant with the feature values provided.

    Args:
        paths (List[List[Tuple[str, str, Any]]]): The paths to check.
        feature_values (Dict[str, Any]): The feature values to check against the paths.
        centroid_threshold (int): The threshold for centroid distance comparison.

    Returns:
        Tuple[bool, Dict[str, Any]]:
            A tuple containing a boolean indicating if the path is compliant with the feature values,
            and a dictionary with the updated features values.
    """
    is_compliant = False  # Determines if the path is compliant with the determined features we expect
    aux_feature_values = (
        feature_values.copy()
    )  # We need an auxialiar copy to avoid modifying the original dictionary

    def check_condition(condition: Tuple[str, str, Any]) -> bool:
        feature, operator, threshold = condition
        _, feature, centroid, _ = read_feature_column_name(feature)  # noqa: F811

        exists_schema = False
        for cond_feature in feature_values:
            if cond_feature[:7] == "or_cond":
                for or_cond_feature in feature_values[cond_feature]:
                    (
                        _,
                        or_cond_feature_name,
                        or_cond_feature_centroid,
                        _,
                    ) = read_feature_column_name(or_cond_feature)
                    if feature == or_cond_feature_name and centroid_distance_checker(
                        centroid, or_cond_feature_centroid, centroid_threshold
                    ):
                        feature_value_comp = feature_values[cond_feature][
                            or_cond_feature
                        ]  # Example [">= 6", "> 5"]
                        aux_feature_values[or_cond_feature] = (
                            aux_feature_values[or_cond_feature] + 1
                            if or_cond_feature in aux_feature_values
                            else 1
                        )
                        exists_schema = True
                        break
            else:
                (
                    _,
                    cond_feature_name,
                    cond_feature_centroid,
                    _,
                ) = read_feature_column_name(cond_feature)
                if feature == cond_feature_name and centroid_distance_checker(
                    centroid, cond_feature_centroid, centroid_threshold
                ):
                    feature_value_comp = feature_values[cond_feature]
                    aux_feature_values[or_cond_feature] = (
                        aux_feature_values[or_cond_feature] + 1
                        if or_cond_feature in aux_feature_values
                        else 1
                    )
                    exists_schema = True
                    break

        if not exists_schema:
            return False

        if type(feature_value_comp) is not list:
            print(
                f"Feature value comparisons should be a list in the format ['>= %d', '> %d'], got {feature_value_comp}"
            )
            print("Falling to feature_value, operator, threshold comparison")
            return eval(f"{feature_value_comp} {operator} {threshold}")
        return f"{operator} {threshold}" in feature_value_comp

    for conditions in paths:
        is_compliant = all(check_condition(condition) for condition in conditions)
        if is_compliant:
            break

    return is_compliant, aux_feature_values


########################################3


def find_prev_act(json_path, decision_point_id):
    try:
        with open(json_path, "r") as file:
            traceability = json.load(file)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
        return None
    except FileNotFoundError as e:
        print(f"File not found: {e}")
        return None

    for dp in traceability.get_non_empty_dp_flattened():
        if dp["id"] == decision_point_id:
            return dp["prevAct"]

    return None


def extract_tree_rules(path_to_tree_file):
    try:
        with open(path_to_tree_file, "rb") as archivo:
            loaded_data = pickle.load(archivo)
        # Obtener el clasificador y los nombres de las características del diccionario cargado
        tree = loaded_data["classifier"]
        feature_names = loaded_data["feature_names"]
        classes = loaded_data["class_names"]

    except FileNotFoundError:
        print(f"File not found: {path_to_tree_file}")
        return None

    """
    Función que recorre las ramas de un árbol de decisión y extrae las reglas
    obtenidas para clasificar cada una de las variables objetivo
    
    Parametros:
    - tree: El modelo árbol de decisión.
    - feature_names: Lista de los atributos del dataset.
    - classes: Clases posibles de la variable objetivo, ordenada ascendentemente
    """
    # Accede al objeto interno tree_ del árbol de decisión
    tree_ = tree.tree_
    feature_name = [
        feature_names[i] if i != _tree.TREE_UNDEFINED else "undefined!"
        for i in tree_.feature
    ]

    # Crear un diccionario para almacenar las reglas de cada clase
    rules_per_class = {cls: [] for cls in classes}

    def recurse(node, parent_rule):
        if tree_.feature[node] != _tree.TREE_UNDEFINED:
            name = feature_name[node]
            threshold = tree_.threshold[node]
            left_rule = parent_rule + [f"{name} <= {threshold:.2f}"]
            right_rule = parent_rule + [f"{name} > {threshold:.2f}"]

            recurse(tree_.children_left[node], left_rule)
            recurse(tree_.children_right[node], right_rule)
        else:
            rule = " & ".join(parent_rule)
            target_index = tree_.value[node].argmax()
            target = classes[target_index] if target_index < len(classes) else None
            if target:
                # Agregar la regla a la lista correspondiente de su clase en el diccionario
                rules_per_class[target].append(rule)

    recurse(0, [])

    # Filtrar las clases que no tienen reglas
    rules_per_class = {k: v for k, v in rules_per_class.items() if v}

    return rules_per_class


def truncar_a_dos_decimales(valor):
    cadena = str(valor)
    partes = cadena.split(".")
    if len(partes) == 2:
        return partes[0] + "." + partes[1][:2]
    else:
        return cadena


def rename_columns_with_centroids(df):
    pattern_with_centroid = (
        r"([a-zA-Z_]+)__([a-zA-Z0-9_-]+)_(\d+\.\d+-\d+\.\d+)_(\d+)(_?[a-zA-Z0-9]*)"
    )
    pattern_no_prefix = r"([a-zA-Z0-9_]+)_(\d+\.\d+-\d+\.\d+)_(\d+)(_?[a-zA-Z0-9]*)"

    new_columns = []

    for column in df.columns:
        matches_with_centroid = re.match(pattern_with_centroid, column)
        matches_no_prefix = re.match(pattern_no_prefix, column)

        if matches_with_centroid:
            suffix = matches_with_centroid.group(1)
            feature = matches_with_centroid.group(2)
            centroid = [
                float(coord) for coord in matches_with_centroid.group(3).split("-")
            ]
            activity = matches_with_centroid.group(4)
            extra = (
                matches_with_centroid.group(5) if matches_with_centroid.group(5) else ""
            )
            centroid_str = f"{truncar_a_dos_decimales(centroid[0])}-{truncar_a_dos_decimales(centroid[1])}"
            new_name = f"{suffix}__{feature}_{centroid_str}_{activity}{extra}"
            new_columns.append(new_name)
        elif matches_no_prefix:
            feature = matches_no_prefix.group(1)
            centroid = [float(coord) for coord in matches_no_prefix.group(2).split("-")]
            activity = matches_no_prefix.group(3)
            extra = matches_no_prefix.group(4) if matches_no_prefix.group(4) else ""
            centroid_str = f"{truncar_a_dos_decimales(centroid[0])}-{truncar_a_dos_decimales(centroid[1])}"
            new_name = f"{feature}_{centroid_str}_{activity}{extra}"
            new_columns.append(new_name)
        else:
            new_columns.append(column)

    df.columns = new_columns
    return df
