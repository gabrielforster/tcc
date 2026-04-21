# Complete Guide: Machine Learning Model for Billet Default Prediction

> Predictive analysis system to identify whether a company is likely to pay or default on an overdue or upcoming billet, using Machine Learning with a microservices architecture.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Solution Architecture](#2-solution-architecture)
3. [Data Preparation](#3-data-preparation)
4. [Model Training](#4-model-training)
5. [Model Serialization and Deployment](#5-model-serialization-and-deployment)
6. [Inference Microservice (ML Engine - Python)](#6-inference-microservice-ml-engine---python)
7. [HTTP Service (API Gateway)](#7-http-service-api-gateway)
8. [How to Run the System](#8-how-to-run-the-system)
9. [How to Test](#9-how-to-test)
10. [Metrics and Evaluation](#10-metrics-and-evaluation)
11. [File Structure](#11-file-structure)

---

## 1. Project Overview

### Problem

Companies frequently issue billets that are not paid on time, leading to default. The goal of this project is to create a Machine Learning model capable of **predicting the probability that a company will pay or default on a billet**, whether it is already overdue or upcoming.

### Approach

- **Binary classification**: The model classifies each billet/company as `WILL_PAY (0)` or `WILL_DEFAULT (1)`.
- **Algorithm**: Random Forest Classifier.
- **Architecture**: Microservices with Docker — an HTTP service for input validation and a Python/FastAPI engine for inference.

### General Flow

```
[Historical Data] -> [Preprocessing] -> [Model Training] -> [model.pkl + scaler.pkl]
                                                                    |
[User/System] -> [HTTP Service :3000] -> [ML Engine (Python) :5000] -> [Prediction: Will Pay / Will Default]
```

---

## 2. Solution Architecture

The system follows a microservices architecture orchestrated by Docker Compose:

```
Client (POST JSON) --> HTTP Service (:3000)
                            |
                     Validated Data
                            |
                     ML Engine (Python/FastAPI :5000)
                            |
                     Prediction + Score
                            |
                     HTTP Service (:3000)
                            |
                     Final Result --> Client
```

| Service | Responsibility |
|---------|---------------|
| **HTTP Service** | Input validation, routing, security |
| **ML Engine** (Python) | Loads `.pkl` model, preprocesses data, returns prediction |

---

## 3. Data Preparation

### 3.1 Suggested Features for Billet Default Prediction

Adapt these features to your dataset:

| Feature | Type | Description |
|---------|------|-------------|
| `company_years_in_market` | float | Years the company has been operating |
| `company_monthly_revenue` | float | Average monthly revenue |
| `company_employee_count` | int | Number of employees |
| `billet_amount` | float | Billet value |
| `billet_days_to_due` | int | Days until due date (negative = overdue) |
| `billet_revenue_ratio` | float | Billet value / Monthly revenue |
| `history_billets_paid` | int | Total billets paid historically |
| `history_billets_late` | int | Total billets paid late |
| `history_billets_defaulted` | int | Total billets never paid |
| `historical_default_rate` | float | % of billets defaulted / total issued |
| `company_score` | int | Company score (e.g. bureau score) |
| `industry_sector` | categorical | Economic sector (manufacturing, retail, services...) |
| `company_size` | categorical | Micro, Small, Medium, Large |
| `region` | categorical | Geographic region |
| `defaulted` | int (target) | **0 = paid, 1 = defaulted** (target variable) |

### 3.2 Preprocessing

```python
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

# 1. Load dataset
df = pd.read_csv('billet_data.csv')

# 2. Handle null values
df = df.dropna()  # or df.fillna() to fill

# 3. One-Hot Encoding for categorical variables
df = pd.get_dummies(df, columns=['industry_sector', 'company_size', 'region'])

# 4. Separate features (X) and target (y)
X = df.drop('defaulted', axis=1)
y = df['defaulted']

# 5. Split into train and test sets
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

# 6. Scale numerical features
numerical_columns = [
    'company_years_in_market', 'company_monthly_revenue',
    'company_employee_count', 'billet_amount', 'billet_days_to_due',
    'billet_revenue_ratio', 'history_billets_paid',
    'history_billets_late', 'history_billets_defaulted',
    'historical_default_rate', 'company_score'
]

scaler = StandardScaler()
X_train[numerical_columns] = scaler.fit_transform(X_train[numerical_columns])
X_test[numerical_columns] = scaler.transform(X_test[numerical_columns])
```

**Important**: Use `stratify=y` in `train_test_split` to maintain the proportion of defaulters in both sets, since default datasets are typically imbalanced.

---

## 4. Model Training

### 4.1 Training with Random Forest

```python
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
import joblib

model = RandomForestClassifier(
    n_estimators=200,        # number of trees
    max_depth=15,            # maximum depth
    min_samples_split=5,     # minimum samples to split a node
    min_samples_leaf=2,      # minimum samples in a leaf
    class_weight='balanced', # IMPORTANT for imbalanced data
    random_state=42,
    n_jobs=-1                # use all CPU cores
)

model.fit(X_train, y_train)

y_pred = model.predict(X_test)
y_proba = model.predict_proba(X_test)[:, 1]

print("=== Classification Report ===")
print(classification_report(y_test, y_pred, target_names=['Will Pay', 'Will Default']))

print("=== Confusion Matrix ===")
print(confusion_matrix(y_test, y_pred))

print(f"AUC-ROC: {roc_auc_score(y_test, y_proba):.4f}")
```

### 4.2 The `class_weight='balanced'` Parameter

In default prediction problems, there are typically far more paid billets than defaulted ones. The `class_weight='balanced'` parameter automatically adjusts class weights inversely proportional to their frequency, preventing the model from simply predicting "will pay" for everything.

### 4.3 Feature Importance

```python
import matplotlib.pyplot as plt

feature_importances = pd.Series(
    model.feature_importances_,
    index=X_train.columns
).sort_values(ascending=False)

print("=== Top 10 Most Important Features ===")
print(feature_importances.head(10))

feature_importances.head(15).plot(kind='barh', figsize=(10, 6))
plt.title('Feature Importance')
plt.tight_layout()
plt.savefig('feature_importances.png')
plt.show()
```

---

## 5. Model Serialization and Deployment

After training, save the model and scaler as `.pkl` files (pickle via joblib):

```python
import joblib

joblib.dump(model, 'ml-engine/model.pkl')
joblib.dump(scaler, 'ml-engine/scaler.pkl')

print("Model and scaler saved successfully!")
```

These two files will be loaded by the ML Engine microservice at runtime.

---

## 6. Inference Microservice (ML Engine - Python)

### 6.1 File: `ml-engine/main.py`

This is the Python service that loads the model and exposes the prediction API:

```python
import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

model = joblib.load('model.pkl')
scaler = joblib.load('scaler.pkl')


class BilletInput(BaseModel):
    company_years_in_market: float
    company_monthly_revenue: float
    company_employee_count: int
    billet_amount: float
    billet_days_to_due: int
    billet_revenue_ratio: float
    history_billets_paid: int
    history_billets_late: int
    history_billets_defaulted: int
    historical_default_rate: float
    company_score: int
    # Categorical (one-hot encoded) - default to 0
    industry_sector_retail: int = 0
    industry_sector_manufacturing: int = 0
    industry_sector_services: int = 0
    industry_sector_technology: int = 0
    company_size_micro: int = 0
    company_size_small: int = 0
    company_size_medium: int = 0
    company_size_large: int = 0
    region_south: int = 0
    region_southeast: int = 0
    region_northeast: int = 0
    region_north: int = 0
    region_midwest: int = 0


@app.get("/")
def health_check():
    return {"status": "ML Engine is running"}


@app.post("/predict")
def predict(data: BilletInput):
    try:
        input_dict = data.dict()
        df = pd.DataFrame([input_dict])

        if hasattr(model, 'feature_names_in_'):
            model_cols = model.feature_names_in_
        else:
            raise ValueError("Model does not have feature_names_in_ attribute")

        for col in model_cols:
            if col not in df.columns:
                df[col] = 0

        df_final = df[model_cols].copy()

        cols_to_scale = [
            'company_years_in_market', 'company_monthly_revenue',
            'company_employee_count', 'billet_amount',
            'billet_days_to_due', 'billet_revenue_ratio',
            'history_billets_paid', 'history_billets_late',
            'history_billets_defaulted', 'historical_default_rate',
            'company_score'
        ]

        df_final[cols_to_scale] = scaler.transform(df_final[cols_to_scale])

        prediction = model.predict(df_final)
        probability = model.predict_proba(df_final)

        prob_pay = float(probability[0][0])
        prob_default = float(probability[0][1])

        return {
            "will_pay": int(prediction[0]) == 0,
            "payment_probability": round(prob_pay, 4),
            "default_probability": round(prob_default, 4),
            "risk_level": (
                "LOW" if prob_default < 0.3
                else "MEDIUM" if prob_default < 0.6
                else "HIGH"
            ),
            "recommendation": (
                "Release billet normally"
                if prob_default < 0.3
                else "Monitor - consider preventive contact"
                if prob_default < 0.6
                else "High risk - consider early collection or guarantees"
            )
        }
    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
```

### 6.2 File: `ml-engine/requirements.txt`

```
fastapi
uvicorn
scikit-learn
pandas
joblib
pydantic
```

### 6.3 File: `ml-engine/Dockerfile`

```dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
```

---

## 7. HTTP Service (API Gateway)

The HTTP service sits in front of the ML Engine. It is responsible for:

- **Input validation**: Ensures all required fields are present and correctly typed before forwarding to the ML Engine.
- **Routing**: Exposes a public endpoint (e.g. `POST /billet/analyze`) and forwards validated data to the internal ML Engine at `POST /predict`.
- **Security**: Handles authentication, rate limiting, and any business rules before inference.

The technology choice for this service is open. It can be built in any language or framework — the only requirement is that it can make HTTP requests to the ML Engine. The contract is:

### 7.1 Request — `POST /billet/analyze`

The HTTP service receives this payload from the client:

```json
{
  "company_years_in_market": 15.0,
  "company_monthly_revenue": 500000.00,
  "company_employee_count": 50,
  "billet_amount": 5000.00,
  "billet_days_to_due": 15,
  "billet_revenue_ratio": 0.01,
  "history_billets_paid": 120,
  "history_billets_late": 3,
  "history_billets_defaulted": 0,
  "historical_default_rate": 0.0,
  "company_score": 850,
  "industry_sector_technology": 1,
  "company_size_medium": 1,
  "region_south": 1
}
```

**Required fields** (numerical):

| Field | Type | Description |
|-------|------|-------------|
| `company_years_in_market` | float | Years the company has been operating |
| `company_monthly_revenue` | float | Average monthly revenue |
| `company_employee_count` | int | Number of employees |
| `billet_amount` | float | Billet value |
| `billet_days_to_due` | int | Days until due date (negative = overdue) |
| `billet_revenue_ratio` | float | Billet value / Monthly revenue |
| `history_billets_paid` | int | Total billets paid historically |
| `history_billets_late` | int | Total billets paid late |
| `history_billets_defaulted` | int | Total billets never paid |
| `historical_default_rate` | float | % of billets defaulted / total issued |
| `company_score` | int | Company bureau score |

**Optional fields** (one-hot encoded categories, default `0`):

| Field | Type |
|-------|------|
| `industry_sector_retail` | int |
| `industry_sector_manufacturing` | int |
| `industry_sector_services` | int |
| `industry_sector_technology` | int |
| `company_size_micro` | int |
| `company_size_small` | int |
| `company_size_medium` | int |
| `company_size_large` | int |
| `region_south` | int |
| `region_southeast` | int |
| `region_northeast` | int |
| `region_north` | int |
| `region_midwest` | int |

### 7.2 Internal Call — HTTP Service to ML Engine

After validating the input, the HTTP service forwards the JSON body as-is to the ML Engine:

```
POST http://ml-engine:5000/predict
Content-Type: application/json
Body: <validated input>
```

### 7.3 Response — ML Engine to Client (via HTTP Service)

The ML Engine returns:

```json
{
  "will_pay": true,
  "payment_probability": 0.92,
  "default_probability": 0.08,
  "risk_level": "LOW",
  "recommendation": "Release billet normally"
}
```

The HTTP service passes this response back to the client.

### 7.4 Docker Compose (`docker-compose.yml`)

```yaml
version: '3.8'

services:
  ml-engine:
    build: ./ml-engine
    container_name: ml_engine_container
    ports:
      - "5000:5000"
    networks:
      - default-net

  http-service:
    build: ./http-service
    container_name: http_service_container
    ports:
      - "3000:3000"
    depends_on:
      - ml-engine
    environment:
      - ML_API_URL=http://ml-engine:5000
    networks:
      - default-net

networks:
  default-net:
    driver: bridge
```

---

## 8. How to Run the System

### Prerequisites

- Docker and Docker Compose installed
- Trained model (`model.pkl` and `scaler.pkl`) inside the `ml-engine/` folder

### Step by Step

```bash
# 1. Make sure model.pkl and scaler.pkl are in ml-engine/
ls ml-engine/model.pkl ml-engine/scaler.pkl

# 2. Start the containers
docker compose up --build

# 3. Verify both services are running
# HTTP Service: http://localhost:3000
# ML Engine:    http://localhost:5000
```

Run in the background:

```bash
docker compose up --build -d
```

Stop:

```bash
docker compose down
```

---

## 9. How to Test

### 9.1 Health Check

```bash
curl http://localhost:5000/

# Expected response:
# {"status": "ML Engine is running"}
```

### 9.2 Test: Company with LOW default risk

```bash
curl -X POST http://localhost:3000/billet/analyze \
-H "Content-Type: application/json" \
-d '{
  "company_years_in_market": 15.0,
  "company_monthly_revenue": 500000.00,
  "company_employee_count": 50,
  "billet_amount": 5000.00,
  "billet_days_to_due": 15,
  "billet_revenue_ratio": 0.01,
  "history_billets_paid": 120,
  "history_billets_late": 3,
  "history_billets_defaulted": 0,
  "historical_default_rate": 0.0,
  "company_score": 850,
  "industry_sector_technology": 1,
  "company_size_medium": 1,
  "region_south": 1
}'
```

**Expected response:**

```json
{
  "will_pay": true,
  "payment_probability": 0.92,
  "default_probability": 0.08,
  "risk_level": "LOW",
  "recommendation": "Release billet normally"
}
```

### 9.3 Test: Company with HIGH default risk

```bash
curl -X POST http://localhost:3000/billet/analyze \
-H "Content-Type: application/json" \
-d '{
  "company_years_in_market": 1.5,
  "company_monthly_revenue": 15000.00,
  "company_employee_count": 2,
  "billet_amount": 12000.00,
  "billet_days_to_due": -30,
  "billet_revenue_ratio": 0.80,
  "history_billets_paid": 5,
  "history_billets_late": 8,
  "history_billets_defaulted": 4,
  "historical_default_rate": 0.47,
  "company_score": 320,
  "industry_sector_retail": 1,
  "company_size_micro": 1,
  "region_northeast": 1
}'
```

**Expected response:**

```json
{
  "will_pay": false,
  "payment_probability": 0.15,
  "default_probability": 0.85,
  "risk_level": "HIGH",
  "recommendation": "High risk - consider early collection or guarantees"
}
```

### 9.4 Test: Company with MEDIUM default risk

```bash
curl -X POST http://localhost:3000/billet/analyze \
-H "Content-Type: application/json" \
-d '{
  "company_years_in_market": 5.0,
  "company_monthly_revenue": 80000.00,
  "company_employee_count": 10,
  "billet_amount": 25000.00,
  "billet_days_to_due": -5,
  "billet_revenue_ratio": 0.31,
  "history_billets_paid": 30,
  "history_billets_late": 10,
  "history_billets_defaulted": 2,
  "historical_default_rate": 0.05,
  "company_score": 580,
  "industry_sector_services": 1,
  "company_size_small": 1,
  "region_southeast": 1
}'
```

**Expected response:**

```json
{
  "will_pay": true,
  "payment_probability": 0.55,
  "default_probability": 0.45,
  "risk_level": "MEDIUM",
  "recommendation": "Monitor - consider preventive contact"
}
```

---

## 10. Metrics and Evaluation

### 10.1 Key Metrics for Default Prediction

| Metric | What it Measures | Why it Matters |
|--------|-----------------|----------------|
| **Precision** | Of those predicted as defaulters, how many actually defaulted | Avoids blocking good payers |
| **Recall** | Of actual defaulters, how many the model caught | Avoids letting defaulters through |
| **F1-Score** | Harmonic mean of Precision and Recall | Overall balance |
| **AUC-ROC** | Model's ability to separate the two classes | Closer to 1.0 is better |
| **Confusion Matrix** | True/False positives and negatives | Visualizes model errors |

### 10.2 Full Evaluation Script

```python
from sklearn.metrics import (
    classification_report, confusion_matrix,
    roc_auc_score, roc_curve
)
import matplotlib.pyplot as plt

y_pred = model.predict(X_test)
y_proba = model.predict_proba(X_test)[:, 1]

print(classification_report(
    y_test, y_pred,
    target_names=['Will Pay (0)', 'Will Default (1)']
))

auc = roc_auc_score(y_test, y_proba)
print(f"AUC-ROC: {auc:.4f}")

fpr, tpr, _ = roc_curve(y_test, y_proba)
plt.figure(figsize=(8, 6))
plt.plot(fpr, tpr, label=f'Random Forest (AUC = {auc:.4f})')
plt.plot([0, 1], [0, 1], 'k--', label='Random')
plt.xlabel('False Positive Rate')
plt.ylabel('True Positive Rate')
plt.title('ROC Curve - Default Prediction Model')
plt.legend()
plt.savefig('roc_curve.png')
plt.show()

cm = confusion_matrix(y_test, y_pred)
print(f"\nConfusion Matrix:")
print(f"  True Negatives  (Paid and predicted Paid):       {cm[0][0]}")
print(f"  False Positives (Paid but predicted Default):    {cm[0][1]}")
print(f"  False Negatives (Defaulted but predicted Paid):  {cm[1][0]}")
print(f"  True Positives  (Defaulted and predicted Default): {cm[1][1]}")
```

### 10.3 Performance Targets

For a production default prediction model, aim for:

- **AUC-ROC** >= 0.80
- **Recall on the default class** >= 0.75 (do not miss more than 25% of actual defaulters)
- **Precision on the default class** >= 0.60 (do not wrongly block more than 40% of good payers)

---

## 11. File Structure

```
tcc/
├── docker-compose.yml
├── GUIA_MODELO_INADIMPLENCIA.md         # This document
│
├── notebooks/                            # Jupyter notebooks for training
│   └── training_model.ipynb
│
├── http-service/                         # HTTP service (language of your choice)
│   ├── src/                              # Validates input, forwards to ML Engine
│   ├── Dockerfile
│   └── ...
│
├── ml-engine/                            # Python inference service
│   ├── model.pkl                         # Trained model (generated in notebook)
│   ├── scaler.pkl                        # Trained scaler (generated in notebook)
│   ├── main.py                           # FastAPI prediction API
│   ├── requirements.txt
│   └── Dockerfile
│
└── references-git/                       # Reference project
    └── N3-ML-Cr-dito/
```

---

## Workflow Summary

1. **Collect** historical billet data with company information
2. **Preprocess**: clean data, categorical encoding, scale numerical features
3. **Train** the Random Forest with `class_weight='balanced'`
4. **Evaluate** with metrics (AUC-ROC, Recall, Precision, F1)
5. **Serialize** model and scaler with `joblib.dump()`
6. **Place** the `.pkl` files in the `ml-engine/` folder
7. **Start** with `docker compose up --build`
8. **Test** via `curl` or Postman at `POST /billet/analyze`
