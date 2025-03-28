import json
import math
import os
import time
import shutil
import graphviz
import numpy as np
import pandas as pd
from django.utils.translation import gettext_lazy as _
from typing import List
import matplotlib.image as plt_img
import matplotlib.pyplot as plt
import scipy
from sklearn.tree import DecisionTreeClassifier, export_graphviz, export_text
from sklearn.metrics import f1_score, accuracy_score, precision_score, recall_score
from .utils import (
    def_preprocessor,
    best_model_grid_search,
    cross_validation,
    extract_tree_rules,
    prev_preprocessor,
)
from apps.chefboost import Chefboost as chef
from apps.notification.views import create_notification
from apps.processdiscovery.utils import Process, Rule
from core.settings import PLOT_DECISION_TREES, SEVERAL_ITERATIONS
import pickle

# def chefboost_decision_tree(df, param_path, algorithms, target_label):
#     """

#     config = {
#     		'algorithm' (string): ID3, 'C4.5, CART, CHAID or Regression
#     		'enableParallelism' (boolean): False
#     		'enableGBM' (boolean): True,
#     		'epochs' (int): 7,
#     		'learning_rate' (int): 1,
#     		'enableRandomForest' (boolean): True,
#     		'num_of_trees' (int): 5,
#     		'enableAdaboost' (boolean): True,
#     		'num_of_weak_classifier' (int): 4
#     	}
#     """
#     times = {}

#     for alg in list(algorithms):
#         df.rename(columns = {target_label:'Decision'}, inplace = True)
#         df['Decision'] = df['Decision'].astype(str) # which will by default set the length to the max len it encounters
#         enableParallelism = False
#         config = {'algorithm': alg, 'enableParallelism': enableParallelism, 'max_depth': 4}# 'num_cores': 2,
#         if SEVERAL_ITERATIONS:
#             durations = []
#             for i in range(0, int(SEVERAL_ITERATIONS)):
#                 start_t = time.time()
#                 model, accuracy_score = chef.fit(df, config = config)
#                 durations.append(float(time.time()) - float(start_t))
#             durations_total = 0
#             for d in durations:
#                 durations_total+=d
#             times[alg] = { "duration": durations_total/len(durations) }
#         else:
#             start_t = time.time()
#             model, accuracy_score = chef.fit(df, config = config)
#             times[alg] = { "duration": float(time.time()) - float(start_t) }
#         # TODO: accurracy_score -> store evaluate terminar output
#         # accuracy_score = chef.evaluate(model, df, "Decision")
#         # model = chef.fit(df, config = config)
#         # output = subprocess.Popen( [chef.evaluate(model,df)], stdout=subprocess.PIPE ).communicate()[0]
#         # file = open(param_path+alg+'-results.txt','w')
#         # file.write(output)
#         # file.close()
#         # Saving model
#         # model = chef.fit(df, config = config, target_label = special_colnames["Variant"])
#         # chef.save_model(model, alg+'model.pkl')
#         # TODO: feature importance
#         fi = chef.feature_importance('outputs/rules/rules.py').set_index("feature")
#         fi.to_csv(param_path+alg+"-tree-feature-importance.csv")
#         # TODO: Graphical representation of feature importance
#         # fi.plot(kind="barh", title="Feature Importance")
#         shutil.move('outputs/rules/rules.py', param_path+alg+'-rules.py')
#         if enableParallelism:
#             shutil.move('outputs/rules/rules.json', param_path+alg+'-rules.json')
#     return accuracy_score, times


def chefboost_decision_tree(
    df,
    prev_act,
    param_path,
    target_label,
    k_fold_cross_validation,
    configuration={
        "algorithms": ["CHAID"],
        "enableParallelism": False,
        "enableGBM": True,
        "epochs": 7,
        "learning_rate": 1,
        "enableRandomForest": True,
        "num_of_trees": 5,
        "enableAdaboost": True,
        "num_of_weak_classifier": 4,
        "max_depth": 5,
    },
):
    """

    config = {
                'algorithms' (string): ID3, 'C4.5, CART, CHAID or Regression
                'enableParallelism' (boolean): False
                'enableGBM' (boolean): True,
                'epochs' (int): 7,
                'learning_rate' (int): 1,
                'enableRandomForest' (boolean): True,
                'num_of_trees' (int): 5,
                'enableAdaboost' (boolean): True,
                'num_of_weak_classifier' (int): 4,
            'max_depth': (int) 5
        }
    """
    times = {}
    accuracies = {}

    algorithms = configuration["algorithms"]

    for alg in list(algorithms):
        df_aux = df.copy()
        df.rename(columns={target_label: "Decision"}, inplace=True)
        target_label = "Decision"
        df["Decision"] = df["Decision"].astype(
            str
        )  # which will by default set the length to the max len it encounters
        config = {
            "algorithm": alg,
            "enableParallelism": configuration["enableParallelism"],
            "max_depth": configuration["max_depth"],
        }  # 'num_cores': 2,
        # Handle nan columns on int and str columns
        for column in df.columns:
            if df[column].dtype == "int64":
                df[column] = df[column].fillna(0)
            if df[column].dtype == "object":
                df[column] = df[column].fillna("")
        if SEVERAL_ITERATIONS:
            durations = []
            for i in range(0, int(SEVERAL_ITERATIONS)):
                start_t = time.time()
                model = chef.fit(df.copy(), config=config, target_label="Decision")
                durations.append(float(time.time()) - float(start_t))
            durations_total = 0
            for d in durations:
                durations_total += d
            times[alg] = {"duration": durations_total / len(durations)}
        else:
            start_t = time.time()
            model, acc = chef.fit(df, config=config, target_label="Decision")
            times[alg] = {"duration": float(time.time()) - float(start_t)}
        # Saving model
        # chef.save_model(model, alg+'model.pkl')

        start_t = time.time()
        model, accuracies = chef.fit(df, config=config, target_label=target_label)
        times[alg] = {"duration": float(time.time()) - float(start_t)}
        model["accuracies"] = accuracies
        # accuracies[alg], model = cross_validation(
        #     X, y, config, target_label, "chefboost", None, k_fold_cross_validation
        # )
        # model["accuracies"] = accuracies[alg]

        # => Feature importance
        fi = chef.feature_importance("outputs/rules/rules.py").set_index("feature")
        fi.to_csv(os.path.join(param_path, alg + "-tree-feature-importance.csv"))
        log_name = f"decision_tree_{prev_act}.log"
        shutil.move("outputs/rules/rules.log", os.path.join(param_path, log_name))
        # TODO: Graphical representation of feature importance
        # fi.plot(kind="barh", title="Feature Importance")

        # shutil.move('outputs/rules/rules.py', param_path+alg+'-rules.py')
        if configuration["enableParallelism"]:
            shutil.move(
                "outputs/rules/rules.json",
                os.path.join(param_path, alg + "-rules.json"),
            )

        features = list(fi.loc[fi["importance"] > 0].index)
        target_labels = list(df[target_label].unique())

        traceability_path = os.path.join(param_path, "traceability.json")
        with open(traceability_path, "r") as f:
            json_data = json.load(f)
            process = Process.from_json(json_data)

        if all(i is not None for i in [features, target_labels]):
            process = update_traceability_chefboost(
                process, features, target_labels, os.path.join(param_path, log_name)
            )
            with open(traceability_path, "w") as f:
                json.dump(process.to_json(), f, indent=2)

        print(param_path)

    return {"CHAID": accuracies}, times


def update_traceability_chefboost(process: Process, features, target_labels, log_path):
    """
    Converts the log in format:
    |--- feature1
    |   |--- feature2
    |   |   |--- feature3
    |   |   |   |--- class: target1
    |   |   |--- feature4
    |   |   |   |--- class: target2

    to the rules in the traceability JSON
    """

    # TODO: Handle multiple instances of the same target
    def get_conditions(target_depth):
        index = features_depth.index(target_depth)
        class_depth = target_depth[1]
        conditions = {}
        for i in range(index - 1, -1, -1):
            item, depth = features_depth[i]
            if depth < class_depth and depth not in conditions:
                conditions[depth] = (item, depth)
        return [conditions[depth] for depth in sorted(conditions)]

    log = open(log_path, "r").readlines()
    # Converts to format [("feature1", depth) ...]
    features_depth = list(
        map(
            lambda l: (
                l.replace("|   ", "").replace("|---", "").strip(),
                int(l.find("|---") / 4),
            ),
            log,
        )
    )

    for decision_point in process.get_non_empty_dp_flattened():
        if all(
            target in list(map(lambda r: r.target, decision_point.rules))
            for target in target_labels
        ):
            for target in target_labels:
                class_str = f"class: {target}"
                target_depth = next(
                    filter(lambda f: f[0] == class_str, features_depth).__iter__(), None
                )
                if target_depth is not None:
                    # Now we find the closest "to the left" items per inferior depth level
                    conditions = get_conditions(target_depth)

    return process


# Ref. https://gist.github.com/j-adamczyk/dc82f7b54d49f81cb48ac87329dba95e#file-graphviz_disk_op-py
def plot_decision_tree(
    path: str,
    clf: DecisionTreeClassifier,
    feature_names: List[str],
    class_names: List[str],
) -> np.ndarray:
    # 1st disk operation: write DOT
    export_graphviz(
        clf,
        out_file=path + ".dot",
        feature_names=feature_names,
        class_names=class_names,
        label="all",
        filled=True,
        impurity=False,
        proportion=True,
        rounded=True,
        precision=2,
    )

    # 2nd disk operation: read DOT
    graph = graphviz.Source.from_file(path + ".dot")

    # 3rd disk operation: write image
    graph.render(path, format="png")

    # 4th disk operation: read image
    image = plt_img.imread(path + ".png")

    # 5th and 6th disk operations: delete files
    os.remove(path + ".dot")
    # os.remove("decision_tree.png")

    return image


def update_json_with_rules(process: Process, rules_class):
    rules_class = {str(k): v for k, v in rules_class.items()}
    # Recorrer los puntos de decisión
    for decision_point in process.get_non_empty_dp_flattened():
        # Recorrer las ramas del punto de decisión
        for branch in decision_point.branches:
            label = branch.label

            # Si hay reglas para esta rama en rules_class_str_keys, actualizar el JSON
            if str(label) in rules_class.keys():
                contained = list(
                    map(lambda r: str(r.target) == str(label), decision_point.rules)
                )
                if any(contained):
                    # Añadir nuevas reglas a la lista existente
                    rule = decision_point.rules[contained.index(True)]
                    rule.condition.extend(rules_class[str(label)])
                else:
                    # Crear una nueva lista de reglas si no existe
                    rule = Rule(rules_class[str(label)], str(label))
                    decision_point.rules.append(rule)

    return process


def sklearn_decision_tree(
    df,
    prevact,
    param_path,
    special_colnames,
    configuration,
    balance_weights,
    one_hot_columns,
    target_label,
    k_fold_cross_validation,
    execution,
):
    times = {}
    accuracies = {}

    # columns_to_encode_one_hot = []
    # for elem in one_hot_columns:
    #     for column_name in list(df.columns):
    #         if elem in column_name:
    #             columns_to_encode_one_hot.append(column_name)
    # if columns_to_encode_one_hot:
    #     df = pd.get_dummies(df, columns=columns_to_encode_one_hot)

    if "e50_" in param_path:
        k_fold_cross_validation = 2

    # Extract features and target variable
    X = df.drop(columns=[special_colnames["Variant"]], errors="ignore").drop(
        columns=["dp_branch"]
    )

    # X = X.astype(str)
    y = df["dp_branch"]
    y = y.astype(str)
    X = prev_preprocessor(X)
    if isinstance(X, str):
        raise Exception(X)
        return

    preprocessor = def_preprocessor(X)

    # df.head()
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

    postprocessor = X_df.columns[X_df.nunique() == 1].tolist()

    if len(postprocessor) != 0:
        X_df = X_df.drop(columns=postprocessor)
    else:
        Exception("No features left after preprocessing.")

    feature_names = X_df.columns.tolist()
    X_df.to_csv(os.path.join(param_path, "preprocessed_df.csv"), header=feature_names)
    # Define the tree decision tree model
    if balance_weights:
        tree_classifier = DecisionTreeClassifier(class_weight="balanced")
    else:
        tree_classifier = DecisionTreeClassifier()
    start_t = time.time()
    try:
        tree_classifier, best_params = best_model_grid_search(
            X_df, y, tree_classifier, k_fold_cross_validation
        )
    except Exception as e:
        scenario = os.path.basename(param_path).split("_results")[0]
        create_notification(
            execution.user,
            "Dataset too small",
            f"Scenario: {scenario} does not have enough data to train a decision tree",
            "/",
            status="warning",
        )
        raise e

    accuracies = cross_validation(
        X_df,
        pd.DataFrame(y),
        None,
        special_colnames["Variant"],
        "sklearn",
        tree_classifier,
        k_fold_cross_validation,
    )
    times["sklearn"] = {"duration": float(time.time()) - float(start_t)}
    # times["sklearn"]["encoders"] = {
    #     "enabled": status_encoder.fit_transform(["enabled"])[0],
    #     "checked": status_encoder.fit_transform(["checked"])[0],
    #      "__empty__": status_encoder.fit_transform([""])[0]
    # }

    # Assuming 'best_tree_tree' is already trained on the training data
    text_representation = export_text(tree_classifier, feature_names=feature_names)
    print("Decision Tree Rules:\n", text_representation)

    with open(
        os.path.join(param_path, "decision_tree_" + prevact + ".log"), "w"
    ) as fout:
        fout.write(text_representation)

    # estimator = clf_model.estimators_[5]
    # export_graphviz(estimator, out_file='tree.dot',
    #                 feature_names = feature_names,
    #                 class_names = target_casted,
    #                 rounded = True, proportion = False,
    #                 precision = 2, filled = True)

    # # Convert to png using system command (requires Graphviz)
    # from subprocess import call
    # call(['dot', '-Tpng', 'tree.dot', '-o', 'tree.png', '-Gdpi=600'])

    # # Display in jupyter notebook
    # from IPython.display import Image
    # Image(filename = 'tree.png')

    saved_data = {
        "classifier": tree_classifier,
        "feature_names": feature_names,
        "class_names": np.unique(y),
        "accuracies": accuracies,
    }
    with open(
        os.path.join(param_path, "decision_tree_" + prevact + ".pkl"), "wb"
    ) as fid:
        pickle.dump(saved_data, fid)

    rules_class = extract_tree_rules(
        os.path.join(param_path, "decision_tree_" + prevact + ".pkl")
    )
    print(rules_class)

    traceability_path = os.path.join(param_path, "traceability.json")

    if rules_class is not None:
        with open(traceability_path, "r") as f:
            json_data = json.load(f)
            process = Process.from_json(json_data)

        process = update_json_with_rules(process, rules_class)

        with open(traceability_path, "w") as f:
            json.dump(process.to_json(), f, indent=2)

    if PLOT_DECISION_TREES:
        target = list(df[target_label].unique())
        target_casted = [str(t) for t in target]
        img = plot_decision_tree(
            os.path.join(param_path, "decision_tree_" + prevact + ".pkl"),
            tree_classifier,
            feature_names,
            target_casted,
        )
        plt.imshow(img)
        plt.show()

    # Grid Search
    accuracies["selected_params"] = best_params

    return accuracies, times
