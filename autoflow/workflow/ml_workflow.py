from typing import Dict

from sklearn.pipeline import Pipeline
from sklearn.utils.metaestimators import if_delegate_has_method
from autoflow.workflow.components.classification_base import AutoFlowClassificationAlgorithm
from autoflow.workflow.components.regression_base import AutoFlowRegressionAlgorithm
from autoflow.manager.data_container.base import DataContainer, copy_data_container_structure
from autoflow.utils.ml_task import MLTask


class ML_Workflow(Pipeline):
    # todo: 现在是用类似拓扑排序的方式实现，但是计算是线性的，希望以后能用更科学的方法！
    # 可以当做Transformer，又可以当做estimator！
    def __init__(self, steps, should_store_intermediate_result=False, resource_manager=None):
        self.resource_manager = resource_manager
        self.should_store_intermediate_result = should_store_intermediate_result
        self.steps = steps
        self.memory = None
        self.verbose = False
        self._validate_steps()
        self.intermediate_result = {}

    # def __reduce__(self):
    #     self.resource_manager = None  # fixme: 防止序列化后数据库连接的元数据泄露
    #     super(ML_Workflow, self).__reduce__()

    @property
    def is_estimator(self):
        is_estimator = False
        if isinstance(self[-1], (AutoFlowClassificationAlgorithm, AutoFlowRegressionAlgorithm)):
            is_estimator = True
        return is_estimator

    def update_data_container_to_dataset_id(self, step_name, name: str, data_container: DataContainer, dict_: Dict[str, str]):
        if data_container is not None:
            data_copied = copy_data_container_structure(data_container)
            data_copied.data=data_container.data
            data_copied.dataset_source = "IntermediateResult"
            task_id = getattr(self.resource_manager, "task_id", "")
            experiment_id = getattr(self.resource_manager, "experiment_id", "")
            data_copied.dataset_metadata.update(
                {"task_id": task_id, "experiment_id": experiment_id, "step_name": step_name})
            data_copied.upload("fs")
            dataset_hash = data_copied.dataset_hash
            dict_[name] = dataset_hash

    def fit(self, X_train, y_train, X_valid=None, y_valid=None, X_test=None, y_test=None, fit_final_estimator=True):
        for (step_idx,
             step_name,
             transformer) in self._iter(with_final=(not self.is_estimator),
                                        filter_passthrough=False):
            fitted_transformer = transformer.fit(X_train, y_train, X_valid, y_valid, X_test, y_test)
            result = transformer.transform(X_train, X_valid, X_test, y_train)
            X_train = result["X_train"]
            X_valid = result.get("X_valid")
            X_test = result.get("X_test")
            y_train = result.get("y_train")
            # if intermediate_result is not None and isinstance(intermediate_result, list):
            #     intermediate_result.append({
            #         "X_train": deepcopy(X_train),
            #         "X_valid": deepcopy(X_valid),
            #         "X_test": deepcopy(X_test),
            #     })
            if self.should_store_intermediate_result:
                current_dict = {}
                self.update_data_container_to_dataset_id(step_name, "X_train", X_train, current_dict)
                self.update_data_container_to_dataset_id(step_name, "X_valid", X_valid, current_dict)
                self.update_data_container_to_dataset_id(step_name, "X_test", X_test, current_dict)
                self.intermediate_result.update({step_name: current_dict})

            self.last_data = result
            self.steps[step_idx] = (step_name, fitted_transformer)
        if fit_final_estimator and self.is_estimator:
            # self._final_estimator.resource_manager = self.resource_manager
            self._final_estimator.fit(X_train, y_train, X_valid, y_valid, X_test, y_test)
            # self._final_estimator.resource_manager = None
        return self

    def fit_transform(self, X_train, y_train=None, X_valid=None, y_valid=None, X_test=None, y_test=None):
        return self.fit(X_train, y_train, X_valid, y_valid, X_test, y_test). \
            transform(X_train, X_valid, X_test, y_train)

    def procedure(self, ml_task: MLTask, X_train, y_train, X_valid=None, y_valid=None, X_test=None, y_test=None):
        self.fit(X_train, y_train, X_valid, y_valid, X_test, y_test)
        X_train = self.last_data["X_train"]
        y_train = self.last_data["y_train"]
        X_valid = self.last_data.get("X_valid")
        X_test = self.last_data.get("X_test")
        self.last_data = None  # GC
        if ml_task.mainTask == "classification":
            pred_valid = self._final_estimator.predict_proba(X_valid)
            pred_test = self._final_estimator.predict_proba(X_test) if X_test is not None else None
        else:
            pred_valid = self._final_estimator.predict(X_valid)
            pred_test = self._final_estimator.predict(X_test) if X_test is not None else None
        self.resource_manager = None
        return {
            "pred_valid": pred_valid,
            "pred_test": pred_test,
            "y_train": y_train  # todo: evaluator 中做相应的改变
        }

    def transform(self, X_train, X_valid=None, X_test=None, y_train=None):
        for _, _, transformer in self._iter(with_final=(not self.is_estimator)):
            result = transformer.transform(X_train, X_valid, X_test, y_train)  # predict procedure
            X_train = result["X_train"]
            X_valid = result.get("X_valid")
            X_test = result.get("X_test")
            y_train = result.get("y_train")
        return {"X_train": X_train, "X_valid": X_valid, "X_test": X_test, "y_train": y_train}

    @if_delegate_has_method(delegate='_final_estimator')
    def predict(self, X):
        result = self.transform(X)
        X = result["X_train"]
        return self.steps[-1][-1].predict(X)

    @if_delegate_has_method(delegate='_final_estimator')
    def predict_proba(self, X):
        result = self.transform(X)
        X = result["X_train"]
        return self.steps[-1][-1].predict_proba(X)
