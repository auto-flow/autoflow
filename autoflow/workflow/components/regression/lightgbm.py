from autoflow.workflow.components.base import BoostingModelMixin
from autoflow.workflow.components.regression_base import AutoFlowRegressionAlgorithm

__all__ = ["LGBMRegressor"]


class LGBMRegressor(AutoFlowRegressionAlgorithm, BoostingModelMixin):
    class__ = "LGBMRegressor"
    module__ = "lightgbm"

    boost_model = True
    tree_model = True
    support_early_stopping = True

    def core_fit(self, estimator, X, y=None, X_valid=None, y_valid=None, X_test=None,
                 y_test=None, feature_groups=None, **kwargs):
        categorical_features_indices = 'auto'  # get_categorical_features_indices(X)
        if (X_valid is not None) and (y_valid is not None):
            eval_set = (X_valid, y_valid)
        else:
            eval_set = None
        early_stopping_rounds = self.hyperparams.get("early_stopping_rounds")
        if eval_set is None:
            early_stopping_rounds = None
        return self.component.fit(
            X, y, categorical_feature=categorical_features_indices,
            eval_set=eval_set, verbose=False,
            early_stopping_rounds=early_stopping_rounds,
            **kwargs
        )
