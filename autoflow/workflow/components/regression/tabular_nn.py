#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author  : qichun tang
# @Contact    : tqichun@gmail.com
# todo 早停策略MixIn
from autoflow.workflow.components.iter_algo import TabularNNIterativeMixIn
from autoflow.workflow.components.regression_base import AutoFlowRegressionAlgorithm

__all__ = ["TabularNNRegressor"]


class TabularNNRegressor(AutoFlowRegressionAlgorithm, TabularNNIterativeMixIn):
    class__ = "TabularNNRegressor"
    module__ = "autoflow.estimator.tabular_nn_est"

    support_early_stopping = True

    def core_fit(self, estimator, X, y=None, X_valid=None, y_valid=None, X_test=None,
                 y_test=None, feature_groups=None, **kwargs):
        categorical_features_indices = None  # todo: 配合 OrdinalEncoder
        component = self.component.fit(
            X, y, X_valid, y_valid, categorical_feature=categorical_features_indices
        )
        self.best_iteration_ = component.best_iteration
        return component
