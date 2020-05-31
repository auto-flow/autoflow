#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author  : qichun tang
# @Contact    : tqichun@gmail.com
from pathlib import Path

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score

import autoflow
from autoflow.feature_engineer.text.tokenize import SimpleTokenizer
from autoflow.feature_engineer.text.topic import LdaTransformer
from autoflow.feature_engineer.text.topic import LsiTransformer
from autoflow.feature_engineer.text.topic import RpTransformer
from autoflow.feature_engineer.text.topic import NmfTransformer
from autoflow.feature_engineer.text.topic import TsvdTransformer

examples_path = Path(autoflow.__file__).parent.parent / "examples"
train_df = pd.read_csv(examples_path / "data/train_classification.csv")
test_df = pd.read_csv(examples_path / "data/test_classification.csv")
y = train_df.pop("Survived")
rf = RandomForestClassifier(random_state=42)

tokenized = SimpleTokenizer().fit_transform(train_df[["Name"]])
print(tokenized)

vectorized = LdaTransformer(num_topics=4).fit_transform(tokenized)
print("--------LDA--------")
score = cross_val_score(rf, vectorized, y)
print(score.mean())

vectorized = LsiTransformer(num_topics=4).fit_transform(tokenized)
print("--------LSI--------")
score = cross_val_score(rf, vectorized, y)
print(score.mean())

vectorized = RpTransformer(num_topics=4).fit_transform(tokenized)
print("--------RP--------")
score = cross_val_score(rf, vectorized, y)
print(score.mean())


vectorized = NmfTransformer(num_topics=4).fit_transform(tokenized)
print("--------NMF--------")
score = cross_val_score(rf, vectorized, y)
print(score.mean())


vectorized = TsvdTransformer(num_topics=4).fit_transform(tokenized)
print("--------TSVD--------")
score = cross_val_score(rf, vectorized, y)
print(score.mean())