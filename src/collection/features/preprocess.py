"""Encoding and scaling, fitted on the training split only (no statistics leakage)."""

import joblib
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from collection.config import settings
from collection.features.build import BOOLEAN, CATEGORICAL, Splits

NON_FEATURES = {"invoice_id", "customer_id", "reference_date"}


def feature_columns(splits: Splits) -> tuple[list[str], list[str]]:
    columns = [c for c in splits.train.columns if c not in NON_FEATURES and c != splits.target]
    categorical = [c for c in columns if c in CATEGORICAL]
    numeric = [c for c in columns if c not in categorical and c not in BOOLEAN]
    return numeric, categorical


def build_preprocessor(splits: Splits) -> ColumnTransformer:
    numeric, categorical = feature_columns(splits)
    numeric_pipeline = Pipeline(
        [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
    )
    categorical_pipeline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    return ColumnTransformer(
        [
            ("num", numeric_pipeline, numeric),
            ("cat", categorical_pipeline, categorical),
            ("bool", "passthrough", [c for c in BOOLEAN if c in splits.train.columns]),
        ],
        remainder="drop",
    )


def fit_and_save(splits: Splits) -> str:
    """Fit the preprocessor on the training split and serialize it for block II."""
    settings.prepare_directories()
    preprocessor = build_preprocessor(splits)
    preprocessor.fit(splits.train)
    path = settings.dir_processed / f"preprocessor_{splits.task}.joblib"
    joblib.dump(preprocessor, path)
    return str(path)
