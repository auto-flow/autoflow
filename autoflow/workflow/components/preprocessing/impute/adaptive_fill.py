#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author  : qichun tang
# @Contact    : tqichun@gmail.com
from autoflow.workflow.components.feature_engineer_base import AutoFlowFeatureEngineerAlgorithm

__all__ = ["AdaptiveSimpleImputer"]


class AdaptiveSimpleImputer(AutoFlowFeatureEngineerAlgorithm):
    class__ = "AdaptiveImputer"
    module__ = "autoflow.feature_engineer.impute"
