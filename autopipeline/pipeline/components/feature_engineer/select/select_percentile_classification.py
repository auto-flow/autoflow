import sklearn.feature_selection

from autopipeline.pipeline.components.feature_engineer.select.select_percentile import SelectPercentileBase

__all__ = ["SelectPercentileClassification"]


class SelectPercentileClassification(SelectPercentileBase):
    def get_default_name(self):
        return "chi2"

    def get_name2func(self):
        return {
            "chi2": sklearn.feature_selection.chi2,
            "f_classif": sklearn.feature_selection.f_classif,
            "mutual_info_classif": sklearn.feature_selection.mutual_info_classif
        }