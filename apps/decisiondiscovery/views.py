import csv
import json
import os
from django.db.models.base import Model as Model
from django.forms import ValidationError
from django.forms.models import model_to_dict
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from art import tprint
import pandas as pd
from apps.chefboost import Chefboost as chef
from apps.analyzer.models import CaseStudy, Execution
from apps.processdiscovery.utils import extract_prev_act_labels, Process
from core.settings import (
    DECISION_FOLDERNAME,
    PLATFORM_NAME,
    FLATTENING_PHASE_NAME,
    DECISION_MODEL_DISCOVERY_PHASE_NAME,
    PROCESS_DISCOVERY_LOG_FILENAME,
)
from core.utils import read_ui_log_as_dataframe
from .models import DecisionTreeTraining, ExtractTrainingDataset
from .forms import DecisionTreeTrainingForm, ExtractTrainingDatasetForm
from .decision_trees import sklearn_decision_tree, chefboost_decision_tree
from .overlapping_rules import overlapping_rules
from .flattening import flat_dataset_row
from .utils import (
    find_paths_to_target_variable,
    find_path_in_decision_tree,
    parse_decision_tree,
)
from django.utils.translation import gettext_lazy as _

# Result Treeimport json
import io
import pickle
import base64
from sklearn.tree import export_graphviz
import pydotplus

# def clean_dataset(df):
#     assert isinstance(df, pd.DataFrame), "df needs to be a pd.DataFrame"
#     df.dropna(inplace=True)
#     indices_to_keep = ~df.isin([np.nan, np.inf, -np.inf]).any(1)
#     return df[indices_to_keep].astype(np.float64)


def extract_training_dataset(log_path, root_path, execution):
    """
    Iterate for every UI log row:
        For each case:
            Store in a map all the attributes of the activities until reaching the decision point
            Assuming the decision point is on activity D, the map would have the following structure:
        {
            "headers": ["timestamp", "MOUSE", "clipboard"...],
            "case1": {"CoorX_A": "value1", "CoorY_A": "value2", ..., "Checkbox_A: "value3",
                        "CoorX_B": "value1", "CoorY_B": "value2", ..., "Checkbox_B: "value3"
                        "CoorX_C": "value1", "CoorY_C": "value2", ..., "Checkbox_C: "value3"
                },
            ...

            "caseN": {"CoorX_A": "value1", "CoorY_A": "value2", ..., "Checkbox_A: "value3",
                        "CoorX_B": "value1", "CoorY_B": "value2", ..., "Checkbox_B: "value3"
                        "CoorX_C": "value1", "CoorY_C": "value2", ..., "Checkbox_C: "value3"
                },
        }
    Once the map is generated, for each case, we concatinate the header with the activity to name the columns and assign them the values
    For each case a new row in the dataframe is generated

    :param decision_point_activity: id of the activity inmediatly previous to the decision point which "why" wants to be discovered
    :type decision_point_activity: str
    :param special_colnames: dict that contains the column names that refers to special columns: "Screenshot", "Variant", "Case"...
    :type special_colnames: dict
    :param columns_to_drop: Names of the colums to remove from the dataset
    :type columns_to_drop: list
    :param log_path: path where ui log to be processed is stored
    :type log_path: str
    :param path_dataset_saved: path where files that results from the flattening are stored
    :type path_dataset_saved: str
    :param actions_columns: list that contains column names that wont be added to the event information just before the decision point
    :type actions_columns: list
    """
    special_colnames = execution.case_study.special_colnames
    actions_columns = (
        execution.extract_training_dataset.columns_to_drop_before_decision_point
    )

    tprint("  " + PLATFORM_NAME + " - " + FLATTENING_PHASE_NAME, "fancy60")
    log = None
    if os.path.exists(os.path.join(root_path + "_results", "pipeline_log.csv")):
        log = read_ui_log_as_dataframe(
            os.path.join(root_path + "_results", "pipeline_log.csv")
        )
    else:
        # Have a fallback log. This one is very light so won't take more than a few miliseconds to read
        try:
            pd_log = read_ui_log_as_dataframe(
                os.path.join(root_path + "_results", PROCESS_DISCOVERY_LOG_FILENAME)
            )
        except Exception as e:
            raise Exception(
                "The "
                + PROCESS_DISCOVERY_LOG_FILENAME
                + " file has not been generated in the path: "
                + root_path
                + "_results: "
                + e
            )
        try:
            fe_log = read_ui_log_as_dataframe(
                os.path.join(root_path + "_results", "log_enriched.csv")
            )
            pd_log = read_ui_log_as_dataframe(
                os.path.join(root_path + "_results", "pd_log.csv")
            )
            cols_to_drop = pd_log.columns.tolist()
            cols_to_drop.remove(execution.case_study.special_colnames["Screenshot"])
            fe_log = fe_log.drop(columns=cols_to_drop, errors="ignore")
            log = pd.merge(
                pd_log,
                fe_log,
                how="inner",
                on=execution.case_study.special_colnames["Screenshot"],
            )
            log.to_csv(os.path.join(root_path + "_results", "pipeline_log.csv"))
            del fe_log
            del pd_log
        except Exception as _:
            log = pd_log
            del pd_log

    process_columns = [
        special_colnames["Case"],
        special_colnames["Activity"],
        special_colnames["Variant"],
        special_colnames["Timestamp"],
        special_colnames["Screenshot"],
        special_colnames["NameApp"],
        special_colnames["EventType"],
        "combined_features",  # Feature vector of the screenshot
        "case:concept:name",  # Case ID Duplicated in Process Discovery
        "concept:name",  # Activity ID Duplicated in Process Discovery
        "time:timestamp",  # Timestamp Duplicated in Process Discovery
    ]

    # We apply filters because iterating and removing will mess up the indices
    columns = list(filter(lambda c: c not in process_columns, log.columns))
    # From the columns of the log, the columns that come from the decision point identification are removed
    # columns = list(filter(lambda c: not re.match(r'id[a-zA-Z0-9]{8}-[a-zA-Z0-9]{4}-[a-zA-Z0-9]{4}-[a-zA-Z0-9]{4}-[a-zA-Z0-9]+', c), columns))
    # for c in columns:
    #     if c in process_columns:
    #         columns.remove(c)
    #     elif "id" in c and re.match(r'id[a-zA-Z0-9]{8}-[a-zA-Z0-9]{4}-[a-zA-Z0-9]{4}-[a-zA-Z0-9]{4}-[a-zA-Z0-9]+', c):
    #         columns.remove(c)

    # Stablish common columns and the rest of the columns are concatinated with "_" + activity
    flat_dataset_row(
        log,
        columns,
        root_path + "_results",
        special_colnames,
        actions_columns,
        execution.process_discovery,
    )


def decision_tree_training(log_path, scenario_path, execution):
    # "media/flattened_dataset.json",
    # "media",
    # "sklearn",
    # ['ID3', 'CART', 'CHAID', 'C4.5'],
    # ["Timestamp_start", "Timestamp_end"],
    # 'Variant',
    # ['NameApp']

    special_colnames = execution.case_study.special_colnames
    implementation = execution.decision_tree_training.library
    configuration = execution.decision_tree_training.configuration
    balance_weights = execution.decision_tree_training.balance_weights
    columns_to_ignore = (
        execution.decision_tree_training.columns_to_drop_before_decision_point
    )
    one_hot_columns = execution.decision_tree_training.one_hot_columns
    k_fold_cross_validation = configuration["cv"] if "cv" in configuration else 3
    algorithms = configuration["algorithms"] if "algorithms" in configuration else None
    centroid_threshold = (
        int(configuration["centroid_threshold"])
        if "centroid_threshold" in configuration
        else None
    )
    feature_values = (
        configuration["feature_values"] if "feature_values" in configuration else None
    )

    if not os.path.exists(os.path.join(scenario_path, DECISION_FOLDERNAME)):
        os.mkdir(os.path.join(scenario_path, DECISION_FOLDERNAME))

    tprint(PLATFORM_NAME + " - " + DECISION_MODEL_DISCOVERY_PHASE_NAME, "fancy60")
    # activities_before_dps=extract_prev_act_labels(os.path.join(scenario_path+"_results","bpmn.dot"))

    try:
        json_traceability = json.load(
            open(os.path.join(scenario_path + "_results", "traceability.json"))
        )
        process_tracebility = Process.from_json(json_traceability)
    except:
        raise Exception("Tracebility.json not found durring dataset flattening")

    decision_points = process_tracebility.get_non_empty_dp_flattened()
    # activities_before_dps= extract_prev_act_labels(os.path.join(path_dataset_saved,"bpmn.dot"))
    activities_before_dps = list(set(map(lambda dp: dp.prevAct, decision_points)))

    res = dict()
    fe_checker = dict()
    times = dict()

    datasets = []
    for act in activities_before_dps:
        datasets.append(
            os.path.join(scenario_path + "_results", f"flattened_dataset_{act}.csv")
        )
        i = 1
        while i != 0:
            if os.path.exists(
                os.path.join(
                    scenario_path + "_results", f"flattened_dataset_{act}-{i}.csv"
                )
            ):
                datasets.append(
                    os.path.join(
                        scenario_path + "_results", f"flattened_dataset_{act}-{i}.csv"
                    )
                )
                i += 1
            else:
                i = 0

    for flattened_csv_log_path in datasets:
        act = flattened_csv_log_path.split("_")[-1].split(".")[0]
        print(flattened_csv_log_path + "\n")

        flattened_dataset = pd.read_csv(flattened_csv_log_path)
        # flattened_dataset.to_csv(path + "flattened_dataset.csv")

        # for col in flattened_dataset.columns:
        #     if "Coor" in col:
        #         columns_to_ignore.append(col)

        # TODO: get type of TextInput column using NLP: convert to categorical variable (conversation, name, email, number, date, etc)
        flattened_dataset = flattened_dataset.drop(
            columns_to_ignore, axis=1, errors="ignore"
        )

        # Drop all columns with 0 variability
        flattened_dataset.drop(
            columns=list(
                filter(
                    lambda c: len(flattened_dataset[c].unique()) == 1,
                    flattened_dataset.columns,
                )
            ),
            inplace=True,
        )

        # flattened_dataset.to_csv(os.path.join(scenario_path+"_results",FLATTENED_DATASET_NAME+".csv"))
        columns_len = flattened_dataset.shape[1]
        # flattened_dataset = flattened_dataset.fillna('NaN')
        # tree_levels = {}

        if implementation == "sklearn":
            try:
                if implementation == "sklearn":
                    res, times = sklearn_decision_tree(
                        flattened_dataset,
                        act,
                        scenario_path + "_results",
                        special_colnames,
                        configuration,
                        balance_weights,
                        one_hot_columns,
                        "Variant",
                        k_fold_cross_validation,
                        execution,
                    )
            except Exception as e:
                print("Error: ", e)
                continue
        elif implementation == "chefboost":
            flattened_dataset.drop(
                columns=[special_colnames["Variant"]], errors="ignore", inplace=True
            )
            res, times = chefboost_decision_tree(
                flattened_dataset,
                act,
                scenario_path + "_results",
                "dp_branch",
                k_fold_cross_validation,
            )
            # TODO: caculate number of tree levels automatically
            # for alg in algorithms:
            # rules_info = open(path+alg+'-rules.json')
            # rules_info_json = json.load(rules_info)
            # tree_levels[alg] = len(rules_info_json.keys())
        elif implementation == "overlapping":
            res, times = overlapping_rules(
                flattened_dataset,
                act,
                scenario_path + "_results",
                special_colnames,
                configuration,
                one_hot_columns,
                "Variant",
                k_fold_cross_validation,
            )

        else:
            raise Exception(_("Decision model chosen is not an option"))

        if feature_values:
            fe_checker = decision_tree_feature_checker(
                feature_values, centroid_threshold, scenario_path + "_results", act
            )
        else:
            fe_checker = None
    return res, fe_checker, times, columns_len  # , tree_levels


def decision_tree_predict(module_path, instance):
    """
    moduleName = "outputs/rules/rules" #this will load outputs/rules/rules.py
    instance = for example ['Sunny', 'Hot', 'High', 'Weak']
    """
    tree = chef.restoreTree(module_path)
    prediction = tree.findDecision(instance)
    return prediction


class ExtractTrainingDatasetCreateView(LoginRequiredMixin, CreateView):
    model = ExtractTrainingDataset
    form_class = ExtractTrainingDatasetForm
    template_name = "extract_training_dataset/create.html"

    # Check if the the phase can be interacted with (included in case study available phases)
    def get(self, request, *args, **kwargs):
        case_study = CaseStudy.objects.get(pk=kwargs["case_study_id"])
        if "ExtractTrainingDataset" in case_study.available_phases:
            return super().get(request, *args, **kwargs)
        else:
            return HttpResponseRedirect(reverse("analyzer:casestudy_list"))

    def post(self, request, *args, **kwargs):
        case_study = CaseStudy.objects.get(pk=kwargs["case_study_id"])
        if "ExtractTrainingDataset" in case_study.available_phases:
            return super().post(request, *args, **kwargs)
        else:
            return HttpResponseRedirect(reverse("analyzer:casestudy_list"))

    def get_context_data(self, **kwargs):
        context = super(ExtractTrainingDatasetCreateView, self).get_context_data(
            **kwargs
        )
        context["case_study_id"] = self.kwargs.get("case_study_id")
        return context

    def form_valid(self, form):
        if not self.request.user.is_authenticated:
            raise PermissionDenied("User must be authenticated.")
        self.object = form.save(commit=False)
        self.object.user = self.request.user
        self.object.case_study = CaseStudy.objects.get(
            pk=self.kwargs.get("case_study_id")
        )
        saved = self.object.save()
        return HttpResponseRedirect(self.get_success_url())


class ExtractTrainingDatasetListView(LoginRequiredMixin, ListView):
    model = ExtractTrainingDataset
    template_name = "extract_training_dataset/list.html"
    paginate_by = 50

    # Check if the the phase can be interacted with (included in case study available phases)
    def get(self, request, *args, **kwargs):
        case_study = CaseStudy.objects.get(pk=kwargs["case_study_id"])
        if "ExtractTrainingDataset" in case_study.available_phases:
            return super().get(request, *args, **kwargs)
        else:
            return HttpResponseRedirect(reverse("analyzer:casestudy_list"))

    def get_context_data(self, **kwargs):
        context = super(ExtractTrainingDatasetListView, self).get_context_data(**kwargs)
        context["case_study_id"] = self.kwargs.get("case_study_id")
        return context

    def get_queryset(self):
        # Obtiene el ID del Experiment pasado como parámetro en la URL
        case_study_id = self.kwargs.get("case_study_id")

        # Search if s is a query parameter
        search = self.request.GET.get("s")
        # Filtra los objetos Extract Training Dataset por case_study_id
        if search:
            queryset = ExtractTrainingDataset.objects.filter(
                case_study__id=case_study_id,
                case_study__user=self.request.user,
                title__icontains=search,
            ).order_by("-created_at")
        else:
            queryset = ExtractTrainingDataset.objects.filter(
                case_study__id=case_study_id, case_study__user=self.request.user
            ).order_by("-created_at")

        return queryset


class ExtractTrainingDatasetDetailView(LoginRequiredMixin, UpdateView):
    model = ExtractTrainingDataset
    form_class = ExtractTrainingDatasetForm
    template_name = "extract_training_dataset/details.html"
    pk_url_kwarg = "extract_training_dataset_id"
    success_url = "/dd/extract-training-dataset/list/"

    def get_object(self, queryset=...):
        return get_object_or_404(
            ExtractTrainingDataset, id=self.kwargs["extract_training_dataset_id"]
        )

    def form_valid(self, form):
        if not self.request.user.is_authenticated:
            raise ValidationError("User must be authenticated.")
        if self.object.freeze:
            raise ValidationError("This object cannot be edited.")
        if not self.object.case_study.user == self.request.user:
            raise PermissionDenied("This object doesn't belong to the authenticated")
        self.object.save()
        return HttpResponseRedirect(
            self.get_success_url() + str(self.object.case_study.id)
        )

    def get(self, request, *args, **kwargs):
        if "case_study_id" in self.kwargs:
            # context['case_study_id'] = self.kwargs['case_study_id']
            case_study = CaseStudy.objects.get(pk=kwargs["case_study_id"])
            if "ExtractTrainingDataset" in case_study.available_phases:
                return super().get(request, *args, **kwargs)
            else:
                return HttpResponseRedirect(reverse("analyzer:casestudy_list"))

        elif "execution_id" in self.kwargs:
            execution = Execution.objects.get(pk=kwargs["execution_id"])
            if execution.extract_training_dataset:
                return super().get(request, *args, **kwargs)
            else:
                return HttpResponseRedirect(reverse("analyzer:execution_list"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # context['case_study_id'] = self.kwargs.get('case_study_id')
        # Set the form with read-only configurations and instance data
        context["form"] = self.form_class(
            initial=model_to_dict(self.object),
            read_only=self.object.freeze,
            instance=self.object,
        )

        if "case_study_id" in self.kwargs:
            context["case_study_id"] = self.kwargs["case_study_id"]

        if "execution_id" in self.kwargs:
            context["execution_id"] = self.kwargs["execution_id"]

        return context

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()  # Ensure the object is fetched
        context = self.get_context_data(**kwargs)
        return render(request, self.template_name, context)

    # def get_object(self, *args, **kwargs):
    #     extract_training_dataset = get_object_or_404(ExtractTrainingDataset, id=kwargs["extract_training_dataset_id"])
    #     return extract_training_dataset
    # return render(request, "extract_training_dataset/details.html", {"extract_training_dataset": extract_training_dataset, "case_study_id": kwargs["case_study_id"]})


def set_as_extracting_training_dataset_active(request):
    extracting_training_dataset_id = request.GET.get("extract_training_dataset_id")
    case_study_id = request.GET.get("case_study_id")
    extracting_training_dataset_list = ExtractTrainingDataset.objects.filter(
        case_study_id=case_study_id
    )
    for m in extracting_training_dataset_list:
        m.active = False
        m.save()
    extracting_training_dataset = ExtractTrainingDataset.objects.get(
        id=extracting_training_dataset_id
    )
    extracting_training_dataset.active = True
    extracting_training_dataset.save()
    return HttpResponseRedirect(
        reverse("decisiondiscovery:extract_training_dataset_list", args=[case_study_id])
    )


def set_as_extracting_training_dataset_inactive(request):
    extracting_training_dataset_id = request.GET.get("extract_training_dataset_id")
    case_study_id = request.GET.get("case_study_id")
    # Validations
    if not request.user.is_authenticated:
        raise PermissionDenied("User must be authenticated.")
    if CaseStudy.objects.get(pk=case_study_id).user != request.user:
        raise PermissionDenied("Case Study doesn't belong to the authenticated user.")
    if (
        ExtractTrainingDataset.objects.get(pk=extracting_training_dataset_id).user
        != request.user
    ):
        raise PermissionDenied(
            "Extracting_training_dataset doesn't belong to the authenticated user."
        )
    if ExtractTrainingDataset.objects.get(
        pk=extracting_training_dataset_id
    ).case_study != CaseStudy.objects.get(pk=case_study_id):
        raise PermissionDenied(
            "Extracting_training_dataset doesn't belong to the Case Study."
        )
    extracting_training_dataset = ExtractTrainingDataset.objects.get(
        id=extracting_training_dataset_id
    )
    extracting_training_dataset.active = False
    extracting_training_dataset.save()
    return HttpResponseRedirect(
        reverse("decisiondiscovery:extract_training_dataset_list", args=[case_study_id])
    )


def delete_extracting_training_dataset(request):
    extracting_training_dataset_id = request.GET.get("extract_training_dataset_id")
    case_study_id = request.GET.get("case_study_id")
    extracting_training_dataset = ExtractTrainingDataset.objects.get(
        id=extracting_training_dataset_id
    )
    if request.user.id != extracting_training_dataset.user.id:
        raise PermissionDenied("This object doesn't belong to the authenticated user")
    extracting_training_dataset.delete()
    return HttpResponseRedirect(
        reverse("decisiondiscovery:extract_training_dataset_list", args=[case_study_id])
    )


##############################################33


class ExtractTrainingDatasetResultDetailView(LoginRequiredMixin, DetailView):
    def get(self, request, *args, **kwargs):
        # Get the Execution object or raise a 404 error if not found
        execution = get_object_or_404(Execution, id=kwargs["execution_id"])
        scenario = request.GET.get("scenario")
        download = request.GET.get("download")
        decision_point = request.GET.get("decision_point")
        # activities_before_dps=execution.process_discovery.activities_before_dps

        if scenario == None:
            # scenario = "1"
            scenario = execution.scenarios_to_study[
                0
            ]  # by default, the first one that was indicated

        activities_before_dps = extract_prev_act_labels(
            os.path.join(
                execution.exp_folder_complete_path, scenario + "_results", "bpmn.dot"
            )
        )

        if decision_point == None:
            # scenario = "1"
            # decision_point = execution.process_discovery.activities_before_dps[0]
            decision_point = activities_before_dps[0]

        # path_to_csv_file = execution.exp_folder_complete_path + "/"+ scenario +"/preprocessed_df.csv"
        path_to_csv_file = os.path.join(
            execution.exp_folder_complete_path,
            scenario + "_results",
            "flattened_dataset_" + decision_point + ".csv",
        )
        # CSV Download
        if path_to_csv_file and download == "True":
            # return ResultDownload(path_to_csv_file)
            return Extract_training_dataset_ResultDownload(path_to_csv_file)

        # CSV Reading and Conversion to JSON
        csv_data_json = read_ui_log_as_dataframe(
            path_to_csv_file, nrows=10, ncols=100, lib="polars"
        ).to_dicts()

        # Include CSV data in the context for the template
        context = {
            "execution_id": execution.id,
            "csv_data": csv_data_json,  # Data to be used in the HTML template
            "scenarios": execution.scenarios_to_study,
            "scenario": scenario,
            "decision_point": decision_point,
            "decision_points": activities_before_dps,
            # "decision_points": execution.process_discovery.activities_before_dps,
        }

        # Render the HTML template with the context including the CSV data
        return render(request, "extract_training_dataset/result.html", context)


##############################################33


# def LogicPhasesResultDetailView(execution, scenario,path_to_csv_file):

#     # CSV Reading and Conversion to JSON
#     csv_data_json = read_csv_to_json(path_to_csv_file)

#     # Include CSV data in the context for the template
#     context = {
#             "execution_id": execution.id,
#             "csv_data": csv_data_json,  # Data to be used in the HTML template
#             "scenarios": execution.scenarios_to_study,
#             "scenario": scenario
#         }
#     return context


#############################################33
def read_csv_to_json(path_to_csv_file):
    # Initialize a list to hold the CSV data converted into dictionaries
    csv_data = []
    # Check if the path to the CSV file exists and read the data
    try:
        with open(path_to_csv_file, "r", newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                csv_data.append(row)
    except FileNotFoundError:
        print(f"File not found: {path_to_csv_file}")
    # Convert csv_data to JSON
    csv_data_json = json.dumps(csv_data)
    return csv_data_json


##########################################3
# descarga solo el csv que visualizas en pantalla (un escenario y un decision point en concreto)
def Extract_training_dataset_ResultDownload(path_to_csv_file):
    if not os.path.exists(path_to_csv_file):
        raise Http404("El archivo no existe")

    with open(path_to_csv_file, "r", newline="") as csvfile:
        # Create an HTTP response with the content of the CSV
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'inline; filename="{}"'.format(
            os.path.basename(path_to_csv_file)
        )
        writer = csv.writer(response)
        reader = csv.reader(csvfile)
        for row in reader:
            writer.writerow(row)
        return response


# descarga todos los flattened_dataset del escenario (de todos los decision points)
# def Extract_training_dataset_ResultDownload(scenario,execution):
#         # Buscar todos los archivos decision_tree_x.pkl en el directorio
#     results_folder = os.path.join(execution.exp_folder_complete_path, scenario + "_results")
#     tree_files = []
#     for root, dirs, files in os.walk(results_folder):
#         for file in files:
#             if file.startswith("flattened_dataset_") and file.endswith(".csv"):
#                 tree_files.append(os.path.join(root, file))

#     if not tree_files:
#         return HttpResponse("No decision tree files found.", status=404)

#     if len(tree_files) == 1:
#         # Si hay solo un archivo, descargarlo directamente
#         file_path = tree_files[0]
#         response = FileResponse(open(file_path, 'rb'), content_type='application/octet-stream')
#         response['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
#         return response
#     else:
#         # Si hay más de un archivo, crear un ZIP y descargarlo
#         zip_filename = f"{scenario}_extract_training_dataset_results.zip"
#         zip_buffer = io.BytesIO()
#         try:
#             with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
#                 for file_path in tree_files:
#                     zip_file.write(file_path, os.path.relpath(file_path, results_folder))

#             zip_buffer.seek(0)

#             # Crear la respuesta HTTP con el archivo ZIP para descargar
#             response = HttpResponse(zip_buffer, content_type="application/zip")
#             response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
#             return response

#         except Exception as e:
#             print(f"An error occurred: {e}")
#             return HttpResponse("Sorry, there was an error processing your request.", status=500)

#############################################################

######################################################
#####################################################3


class DecisionTreeTrainingCreateView(LoginRequiredMixin, CreateView):
    model = DecisionTreeTraining
    form_class = DecisionTreeTrainingForm
    template_name = "decision_tree_training/create.html"

    # Check if the the phase can be interacted with (included in case study available phases)
    def get(self, request, *args, **kwargs):
        case_study = CaseStudy.objects.get(pk=kwargs["case_study_id"])
        if "DecisionTreeTraining" in case_study.available_phases:
            return super().get(request, *args, **kwargs)
        else:
            return HttpResponseRedirect(reverse("analyzer:casestudy_list"))

    def post(self, request, *args, **kwargs):
        case_study = CaseStudy.objects.get(pk=kwargs["case_study_id"])
        if "DecisionTreeTraining" in case_study.available_phases:
            return super().post(request, *args, **kwargs)
        else:
            return HttpResponseRedirect(reverse("analyzer:casestudy_list"))

    def get_context_data(self, **kwargs):
        context = super(DecisionTreeTrainingCreateView, self).get_context_data(**kwargs)
        context["case_study_id"] = self.kwargs.get("case_study_id")
        return context

    def form_valid(self, form):
        if not self.request.user.is_authenticated:
            raise PermissionDenied("User must be authenticated.")
        self.object = form.save(commit=False)
        self.object.user = self.request.user
        self.object.case_study = CaseStudy.objects.get(
            pk=self.kwargs.get("case_study_id")
        )
        saved = self.object.save()
        return HttpResponseRedirect(self.get_success_url())


class DecisionTreeTrainingListView(LoginRequiredMixin, ListView):
    model = DecisionTreeTraining
    template_name = "decision_tree_training/list.html"
    paginate_by = 50

    # Check if the the phase can be interacted with (included in case study available phases)
    def get(self, request, *args, **kwargs):
        case_study = CaseStudy.objects.get(pk=kwargs["case_study_id"])
        if "DecisionTreeTraining" in case_study.available_phases:
            return super().get(request, *args, **kwargs)
        else:
            return HttpResponseRedirect(reverse("analyzer:casestudy_list"))

    def get_context_data(self, **kwargs):
        context = super(DecisionTreeTrainingListView, self).get_context_data(**kwargs)
        context["case_study_id"] = self.kwargs.get("case_study_id")
        return context

    def get_queryset(self):
        # Obtiene el ID del Experiment pasado como parámetro en la URL
        case_study_id = self.kwargs.get("case_study_id")

        # Search if s is a query parameter
        search = self.request.GET.get("s")
        # Filtra los objetos Decision Tree Training por case_study_id
        if search:
            queryset = DecisionTreeTraining.objects.filter(
                case_study__id=case_study_id,
                case_study__user=self.request.user,
                title__icontains=search,
            ).order_by("-created_at")
        else:
            queryset = DecisionTreeTraining.objects.filter(
                case_study__id=case_study_id, case_study__user=self.request.user
            ).order_by("-created_at")

        return queryset


class DecisionTreeTrainingDetailView(LoginRequiredMixin, UpdateView):
    login_url = "/login/"
    model = DecisionTreeTraining
    form_class = DecisionTreeTrainingForm
    template_name = "decision_tree_training/detail.html"
    pk_url_kwarg = "decision_tree_training_id"
    success_url = "/dd/decision-tree-training/list/"

    def form_valid(self, form):
        if not self.request.user.is_authenticated:
            raise ValidationError("User must be authenticated.")
        if self.object.freeze:
            raise ValidationError("This object cannot be edited.")
        if not self.object.case_study.user == self.request.user:
            raise PermissionDenied("This object doesn't belong to the authenticated")
        self.object.save()
        return HttpResponseRedirect(
            self.get_success_url() + str(self.object.case_study.id)
        )

    def get(self, request, *args, **kwargs):
        decision_tree_training = get_object_or_404(
            DecisionTreeTraining, id=kwargs["decision_tree_training_id"]
        )
        form = DecisionTreeTrainingForm(
            read_only=decision_tree_training.freeze, instance=decision_tree_training
        )
        if "case_study_id" in kwargs:
            case_study = get_object_or_404(CaseStudy, id=kwargs["case_study_id"])
            if "DecisionTreeTraining" in case_study.available_phases:
                context = {
                    "decision_tree_training": decision_tree_training,
                    "case_study_id": case_study.id,
                    "form": form,
                }

                return render(request, "decision_tree_training/detail.html", context)
            else:
                return HttpResponseRedirect(reverse("analyzer:casestudy_list"))

        elif "execution_id" in kwargs:
            execution = get_object_or_404(Execution, id=kwargs["execution_id"])
            if execution.decision_tree_training:
                context = {
                    "decision_tree_training": decision_tree_training,
                    "execution_id": execution.id,
                    "form": form,
                }

                return render(request, "decision_tree_training/detail.html", context)
            else:
                return HttpResponseRedirect(reverse("analyzer:execution_list"))


def set_as_decision_tree_training_active(request):
    decision_tree_training_id = request.GET.get("decision_tree_training_id")
    case_study_id = request.GET.get("case_study_id")
    decision_tree_training_list = DecisionTreeTraining.objects.filter(
        case_study_id=case_study_id
    )
    for m in decision_tree_training_list:
        m.active = False
        m.save()
    decision_tree_training = DecisionTreeTraining.objects.get(
        id=decision_tree_training_id
    )
    decision_tree_training.active = True
    decision_tree_training.save()
    return HttpResponseRedirect(
        reverse("decisiondiscovery:decision_tree_training_list", args=[case_study_id])
    )


def set_as_decision_tree_training_inactive(request):
    decision_tree_training_id = request.GET.get("decision_tree_training_id")
    case_study_id = request.GET.get("case_study_id")
    # Validations
    if not request.user.is_authenticated:
        raise PermissionDenied("User must be authenticated.")
    if CaseStudy.objects.get(pk=case_study_id).user != request.user:
        raise PermissionDenied("Case Study doesn't belong to the authenticated user.")
    if (
        DecisionTreeTraining.objects.get(pk=decision_tree_training_id).user
        != request.user
    ):
        raise PermissionDenied(
            "Decision Tree Training doesn't belong to the authenticated user."
        )
    if DecisionTreeTraining.objects.get(
        pk=decision_tree_training_id
    ).case_study != CaseStudy.objects.get(pk=case_study_id):
        raise PermissionDenied(
            "Decision Tree Training doesn't belong to the Case Study."
        )
    decision_tree_training = DecisionTreeTraining.objects.get(
        id=decision_tree_training_id
    )
    decision_tree_training.active = False
    decision_tree_training.save()
    return HttpResponseRedirect(
        reverse("decisiondiscovery:decision_tree_training_list", args=[case_study_id])
    )


def delete_decision_tree_training(request):
    decision_tree_training_id = request.GET.get("decision_tree_training_id")
    case_study_id = request.GET.get("case_study_id")
    decision_tree_training = DecisionTreeTraining.objects.get(
        id=decision_tree_training_id
    )
    if request.user.id != decision_tree_training.user.id:
        raise PermissionDenied("This object doesn't belong to the authenticated user")
    decision_tree_training.delete()
    return HttpResponseRedirect(
        reverse("decisiondiscovery:decision_tree_training_list", args=[case_study_id])
    )


def decision_tree_feature_checker(
    feature_values, centroid_threshold, path, previous_dp_activity
):
    """

    A function to check conditions over decision tree representations

    Args:
        feature_values (dict): Classes and values of the features that should appear in the decision tree to reach this class

        Ejemplo:
         "feature_values": {
                    "1": {
                    "sta_enabled_717.5-606.5_2_B": 0.3,
                    "sta_checked_649.0-1110.5_4_D": 0.2
                    },
                    "2": {
                        "sta_enabled_717.5-606.5_2_B": 0.7,
                        "sta_checked_649.0-1110.5_4_D": 0.2
                    }
                }

    Returns:
        boolean: indicates if the path drives to the correct class

        dict: indicates how many times a feature appears in the decision tree. Example: {
            1: {
                'status_categorical__sta_enabled_717.5-606.5_2_B': 2,
                'status_categorical__sta_checked_649.0-1110.5_4_D': 1
            },
            2: {
                'status_categorical__sta_enabled_717.5-606.5_2_B': 1
            }
        }
    """
    dt_file = os.path.join(path, "decision_tree_" + previous_dp_activity + ".log")

    metadata = {}
    tree, max_depth = parse_decision_tree(dt_file)
    paths = find_paths_to_target_variable(tree)
    for target_class, fe_values_class in feature_values.items():
        current_paths = [p["path"] for p in paths if p["target"] == target_class]
        # TODO: Adapt the called function to work with the new format of inputs. trees -> paths
        path_exists, features_in_tree = find_path_in_decision_tree(
            current_paths, fe_values_class, centroid_threshold
        )
        metadata[target_class] = features_in_tree
        metadata[target_class]["tree_depth"] = max_depth
        metadata[target_class]["cumple_condicion"] = path_exists
    # print(path_exists)
    # print((len(features_in_tree) / len(feature_values))*100)
    return metadata


class DecisionTreeResultDetailView(LoginRequiredMixin, DetailView):
    def get(self, request, *args, **kwargs):
        execution = get_object_or_404(Execution, id=kwargs["execution_id"])
        scenario = request.GET.get("scenario")
        decision_point = request.GET.get("decision_point")
        # activities_before_dps=execution.process_discovery.activities_before_dps

        if scenario == None:
            # scenario = "1"
            scenario = execution.scenarios_to_study[
                0
            ]  # by default, the first one that was indicated

        try:
            json_traceability = json.load(
                open(
                    os.path.join(
                        execution.exp_folder_complete_path,
                        scenario + "_results",
                        "traceability.json",
                    )
                )
            )
            process_tracebility = Process.from_json(json_traceability)
        except:
            raise Exception("Tracebility.json not found")

        decision_points = process_tracebility.get_non_empty_dp_flattened()
        activities_before_dps = list(map(lambda dp: dp.prevAct, decision_points))
        dp_rules = list(map(lambda dp: dp.to_json()["rules"], decision_points))

        count_dict = {}
        tmp = []
        # Converting ['3', '3', '3', '10'] to ['3', '3-1', '3-2', '10']
        for act in activities_before_dps:
            if act in count_dict:
                count_dict[act] += 1
                tmp.append(f"{act}-{count_dict[act] - 1}")
            else:
                count_dict[act] = 1
                tmp.append(act)
        activities_before_dps = tmp
        del tmp

        if decision_point == None:
            # scenario = "1"
            # decision_point = execution.process_discovery.activities_before_dps[0]
            decision_point = activities_before_dps[0]

        path_to_tree_file = os.path.join(
            execution.exp_folder_complete_path,
            scenario + "_results",
            "decision_tree_" + decision_point + ".pkl",
        )
        tree_image_base64 = tree_to_png_base64(path_to_tree_file)
        # tree_rules= extract_tree_rules(path_to_tree_file)

        # Find the decision point with the matching prevAct
        tree_rules = dp_rules[activities_before_dps.index(decision_point)]

        # Include CSV data in the context for the template
        context = {
            "execution_id": execution.id,
            "tree_to_png": tree_image_base64,  # Png to be used in the HTML template
            "scenarios": execution.scenarios_to_study,
            "scenario": scenario,
            "tree_rules": tree_rules,
            "tree_overlapping_rules": {},
            "decision_point": decision_point,
            "decision_points": activities_before_dps,
        }

        return render(request, "decision_tree_training/result.html", context)

    # /screenrpa/apps/templates/


####################################################################


#    http://127.0.0.1:8000/es/case-study/execution/decision_tree_result/33/
def tree_to_png_base64(path_to_tree_file):
    try:
        with open("/screenrpa/" + path_to_tree_file, "rb") as archivo:
            loaded_data = pickle.load(archivo)

        clasificador_loaded = loaded_data["classifier"]
        feature_names_loaded = loaded_data["feature_names"]

        class_names_loaded = loaded_data["class_names"]
    except FileNotFoundError:
        print(f"File not found: {path_to_tree_file}")
        return None

    dot_data = io.StringIO()

    export_graphviz(
        clasificador_loaded,
        out_file=dot_data,
        filled=True,
        rounded=True,
        special_characters=True,
        feature_names=feature_names_loaded,
        class_names=class_names_loaded,
    )

    graph = pydotplus.graph_from_dot_data(dot_data.getvalue())
    png_image = graph.create_png()

    image_base64 = base64.b64encode(png_image).decode("utf-8")

    return f"data:image/png;base64,{image_base64}"


####################################################################


####################################################################
# descarga solo el decision tree del escenario y de decision point que se visualiza en pantalla
def DecisionTreeDownload(request, execution_id):
    execution = get_object_or_404(Execution, pk=execution_id)
    scenario = request.GET.get("scenario")
    decision_point = request.GET.get("decision_point")

    # Construye la ruta al archivo
    path_to_tree_file = os.path.join(
        execution.exp_folder_complete_path,
        scenario + "_results",
        "decision_tree_" + decision_point + ".pkl",
    )

    # Verifica si el archivo existe
    if not os.path.exists(path_to_tree_file):
        raise Http404("El archivo no existe")

    # Abre el archivo y prepara la respuesta HTTP para la descarga
    with open(path_to_tree_file, "rb") as file:
        response = HttpResponse(file.read(), content_type="application/octet-stream")
        response["Content-Disposition"] = (
            f"attachment; filename={os.path.basename(path_to_tree_file)}"
        )
        return response


# descarga todos los decision tree del escenario (de todos los decision points)

# def DecisionTreeDownload(request, execution_id):
#     execution = get_object_or_404(Execution, pk=execution_id)
#     scenario = request.GET.get('scenario')

#     if scenario is None:
#         scenario = execution.scenarios_to_study[0]  # Por defecto, el primero indicado

#     results_folder = os.path.join(execution.exp_folder_complete_path, scenario + "_results")

#     # Buscar todos los archivos decision_tree_x.pkl en el directorio
#     tree_files = []
#     for root, dirs, files in os.walk(results_folder):
#         for file in files:
#             if file.startswith("decision_tree_") and file.endswith(".pkl"):
#                 tree_files.append(os.path.join(root, file))

#     if not tree_files:
#         return HttpResponse("No decision tree files found.", status=404)

#     if len(tree_files) == 1:
#         # Si hay solo un archivo, descargarlo directamente
#         file_path = tree_files[0]
#         response = FileResponse(open(file_path, 'rb'), content_type='application/octet-stream')
#         response['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
#         return response
#     else:
#         # Si hay más de un archivo, crear un ZIP y descargarlo
#         zip_filename = f"{scenario}_decision_tree_results.zip"
#         zip_buffer = io.BytesIO()
#         try:
#             with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
#                 for file_path in tree_files:
#                     zip_file.write(file_path, os.path.relpath(file_path, results_folder))

#             zip_buffer.seek(0)

#             # Crear la respuesta HTTP con el archivo ZIP para descargar
#             response = HttpResponse(zip_buffer, content_type="application/zip")
#             response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
#             return response

#         except Exception as e:
#             print(f"An error occurred: {e}")
#             return HttpResponse("Sorry, there was an error processing your request.", status=500)


####################################################################
