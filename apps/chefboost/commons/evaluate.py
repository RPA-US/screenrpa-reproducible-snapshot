import math
import numpy as np
from django.utils.translation import gettext_lazy as _


def evaluate(df, task="train"):
    acc_total = dict()

    if df["Decision"].dtypes == "object":
        problem_type = "classification"
    else:
        problem_type = "regression"

    # -------------------------------------

    instances = df.shape[0]

    print("-------------------------")
    print(_("Evaluate %(task) set") % {"task": task})
    print("-------------------------")

    if problem_type == "classification":
        idx = df[df["Prediction"] == df["Decision"]].index
        accuracy = 100 * len(idx) / df.shape[0]
        print("Accuracy: ", accuracy, "% on ", instances, " instances")

        # -----------------------------

        predictions = df.Prediction.values
        actuals = df.Decision.values

        # -----------------------------
        # confusion matrix

        # labels = df.Prediction.unique()
        labels = df.Decision.unique()

        confusion_matrix = []
        for prediction_label in labels:
            confusion_row = []
            for actual_label in labels:
                item = len(
                    df[
                        (df["Prediction"] == prediction_label)
                        & (df["Decision"] == actual_label)
                    ]["Decision"].values
                )
                confusion_row.append(item)
            confusion_matrix.append(confusion_row)

        print(_("Labels: "), labels)
        print(_("Confusion matrix: "), confusion_matrix)

        # -----------------------------
        # precision and recall

        precision = []
        recall = []
        f1_score = []
        accuracy = []
        for decision_class in labels:
            fp = 0
            fn = 0
            tp = 0
            tn = 0
            for i in range(0, len(predictions)):
                prediction = predictions[i]
                actual = actuals[i]

                if actual == decision_class and prediction == decision_class:
                    tp = tp + 1
                elif actual != decision_class and prediction != decision_class:
                    tn = tn + 1
                elif actual != decision_class and prediction == decision_class:
                    fp = fp + 1
                elif actual == decision_class and prediction != decision_class:
                    fn = fn + 1

            epsilon = 0.0000001  # to avoid divison by zero exception
            precision.append(round(100 * tp / (tp + fp + epsilon), 4))
            recall.append(round(100 * tp / (tp + fn + epsilon), 4))  # tpr)
            f1_score.append(
                round(
                    (2 * precision[-1] * recall[-1])
                    / (precision[-1] + recall[-1] + epsilon),
                    4,
                )
            )
            accuracy.append(round(100 * (tp + tn) / (tp + tn + fp + fn + epsilon), 4))

            if len(labels) >= 3:
                print(_("Decision "), decision_class, " => ", end="")
                print(_("Accuracy: "), accuracy[-1], "%, ", end="")
                # print("tp:"+str(tp)+", tn:"+str(tn)+", fp:"+str(fp)+", fn:"+str(fn))

            print(
                _(
                    f"Precision: {precision[-1]}, Recall: {recall[-1]}, F1: {f1_score[-1]}"
                )
            )
            # print("TP: ",tp,", TN: ",tn,", FP: ", fp,", FN: ",fn)

            if len(labels) < 3:
                break

        acc_total["accuracy"] = np.mean(accuracy)
        acc_total["precision"] = np.mean(precision)
        acc_total["recall"] = np.mean(recall)
        acc_total["f1_score"] = np.mean(f1_score)

    # -------------------------------------
    else:
        df["Absolute_Error"] = abs(df["Prediction"] - df["Decision"])
        df["Absolute_Error_Squared"] = df["Absolute_Error"] * df["Absolute_Error"]
        df["Decision_Squared"] = df["Decision"] * df["Decision"]
        df["Decision_Mean"] = df["Decision"].mean()
        # print(df)

        if instances > 0:
            mae = df["Absolute_Error"].sum() / instances
            print("MAE: ", mae)

            mse = df["Absolute_Error_Squared"].sum() / instances
            print("MSE: ", mse)

            rmse = math.sqrt(mse)
            print("RMSE: ", rmse)

            rae = 0
            rrse = 0
            try:  # divisor might be equal to 0.
                rae = math.sqrt(df["Absolute_Error_Squared"].sum()) / math.sqrt(
                    df["Decision_Squared"].sum()
                )

                rrse = math.sqrt(
                    (df["Absolute_Error_Squared"].sum())
                    / ((df["Decision_Mean"] - df["Decision"]) ** 2).sum()
                )

            except Exception as err:
                print(str(err))

            print("RAE: ", rae)
            print("RRSE: ", rrse)

            mean = df["Decision"].mean()
            print("Mean: ", mean)

            if mean > 0:
                print("MAE / Mean: ", 100 * mae / mean, "%")
                print("RMSE / Mean: ", 100 * rmse / mean, "%")

            acc_total["MAE"] = mae
            acc_total["MSE"] = mse
            acc_total["RMSE"] = rmse
            acc_total["RAE"] = rae
            acc_total["RRSE"] = rrse
            acc_total["Mean"] = mean
            acc_total["MAE / Mean"] = 100 * mae / mean
            acc_total["RMSE / Mean"] = 100 * rmse / mean

    return acc_total
