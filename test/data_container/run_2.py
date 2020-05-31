#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author  : qichun tang
# @Contact    : tqichun@gmail.com
from pathlib import Path

import joblib
import pandas as pd
from sklearn.model_selection import KFold

import autoflow
from autoflow import AutoFlowClassifier

examples_path = Path(autoflow.__file__).parent.parent / "examples"
train_df = pd.read_csv(examples_path / "data/train_classification.csv")
test_df = pd.read_csv(examples_path / "data/test_classification.csv")
trained_pipeline = AutoFlowClassifier(
    initial_runs=1, run_limit=1, n_jobs=1,
    included_classifiers=["lightgbm"], debug=True,
    should_store_intermediate_result=True,
)
# if not os.path.exists("autoflow_classification.bz2"):
trained_pipeline.fit(
    X_train="53504d6c7acf98fb1fceca330d4ab4bc", X_test="bd5594be7ff050661d3472bcd544bee0",
    splitter=KFold(n_splits=3, shuffle=True, random_state=42)
)
joblib.dump(trained_pipeline, "autoflow_classification.bz2")
predict_pipeline = joblib.load("autoflow_classification.bz2")
result = predict_pipeline.predict(test_df)
print(result)