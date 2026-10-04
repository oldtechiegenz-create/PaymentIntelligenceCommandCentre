You are a Principal Payment Domain Architect and Lead Machine Learning Engineer specializing in cross-border correspondent banking systems, ISO 20022 message standards (pacs.008, pain.001), and quantitative routing engines. 

### Objective
Design and implement a end-to-end Payment Corridor Recommendation Engine using LightGBM. Given an incoming cross-border payment request (e.g., Debtor, Creditor, Origin Bank, Beneficiary Bank, Currency Pair, Amount, Execution Time), the engine must:
1. Predict candidate multi-hop corridors/paths (e.g., B1 -> Intermediary 1 -> Intermediary 2 -> Beneficiary Bank).
2. Predict estimated End-to-End Latency (Turnaround Time - TAT in hours/minutes) for each corridor using historical performance.
3. Estimate Total Cost per corridor, factoring in intermediary lifting fees, network fees, and real-time/predicted FX margins.
4. Output a ranked list of corridors (Cheapest, Fastest, Balanced/Optimal) with clear trade-offs, enabling the corporate customer or treasury manager to make an informed choice.

---

### Architecture & Technical Requirements

#### 1. Machine Learning Pipeline (LightGBM Multi-Model Architecture)
Implement three interconnected LightGBM models (or a multi-output pipeline):

- **Model A: Corridor Path Ranker / Classifier (`LightGBM Classifier / Ranker`)**
  - **Goal:** Predict the top $K$ viable, active corridors for a given debtor-creditor pair and payment context.
  - **Objective Function:** `multiclass` or `lambdarank`.

- **Model B: Latency Regressor (`LightGBM Regressor`)**
  - **Goal:** Predict end-to-end turnaround time (TAT) in minutes for each candidate corridor.
  - **Objective Function:** `regression` (e.g., Huber loss or MAE to handle outliers caused by compliance holds/repair queues).

- **Model C: Intermediary Fee & FX Spread Predictor (`LightGBM Regressor` + Deterministic Rules Engine)**
  - **Goal:** Predict variable intermediary lifting fees per node and expected FX spread slippage.
  - **Logic:** Combine historical fee behavior prediction with deterministic fee schedules and real-time FX rate feeds.

---

#### 2. Feature Engineering & Input Data Model
The engine must build and consume features categorized as follows:

1. **Transaction Context (from `pain.001` / `pacs.008`):**
   - Origin Country & Bank (Debtor Agent BIC)
   - Destination Country & Bank (Creditor Agent BIC)
   - Currency Pair (Source & Target Currency)
   - Transfer Amount (log-transformed & normalized)
   - Time Features: Time of day, day of week, day of month, quarter-end flag, timezone delta between BICs.

2. **Network & Operational Features:**
   - Intermediary Node Count (Hop depth: 1-hop, 2-hop, 3-hop).
   - Clearing System Cut-off Windows (e.g., Fedwire, TARGET2, RTGS/NEFT operating hours). Time remaining until next cut-off along each node in the path.
   - Weekend / Local Holiday Flags for all intermediary jurisdictions.
   - Historical Corridor Volume & Success Rate (Rolling 7d/30d/90d completion rates).

3. **Cost & FX Features:**
   - Benchmark FX Spot Rate vs. Historical FX Spread Margin charged by intermediary nodes.
   - Fixed vs. Tiered Variable Lifting Fees per intermediary BIC.
   - Charge Type Code (`OUR`, `BEN`, `SHA`).

---

#### 3. Routing Optimization Logic (Pareto Ranking Engine)
The engine must score and rank candidate corridors based on user preferences using a multi-objective utility function:

$$\text{Utility Score} = - \left( w_c \cdot \hat{\text{Cost}}_{\text{normalized}} + w_l \cdot \hat{\text{Latency}}_{\text{normalized}} \right) + w_r \cdot \text{Reliability}$$

Where:
- $w_c, w_l, w_r$ are weightings based on user-selected mode (`Fastest`, `Cheapest`, `Recommended/Balanced`).
- $\hat{\text{Cost}}$ includes: Intermediary Lifting Fees + Network Clearing Fees + FX Spread Cost.
- $\hat{\text{Latency}}$ is predicted end-to-end settlement time.
- $\text{Reliability}$ is historical completion rate without repair/rejection.

---

#### 4. System Output & API Contract

Provide a clean REST API specification (JSON schema) that returns recommendations structured like this:

```json
{
  "transaction_id": "TXN-20261004-9981",
  "source_currency": "USD",
  "target_currency": "INR",
  "instructed_amount": 100000.00,
  "recommended_default": "CORRIDOR_BALANCED",
  "options": [
    {
      "option_id": "CORRIDOR_FASTEST",
      "corridor_code": "DIRECT_CORR_01",
      "path_nodes": [
        {"step": 1, "bic": "USBAUS33XXX", "role": "Debtor Agent", "name": "Bank B1 (USA)"},
        {"step": 2, "bic": "SBININBBXXX", "role": "Creditor Agent", "name": "State Bank of India"}
      ],
      "estimated_metrics": {
        "turnaround_time_minutes": 45,
        "estimated_completion_time": "2026-10-04T01:00:00Z",
        "fx_rate_applied": 83.42,
        "gross_fx_amount_target": 8342000.00,
        "lifting_fees_usd": 35.00,
        "fx_margin_cost_usd": 18.00,
        "total_cost_usd": 53.00
      },
      "confidence_score": 0.94,
      "tradeoff_summary": "Direct path with higher lifting fees but fastest settlement."
    },
    {
      "option_id": "CORRIDOR_CHEAPEST",
      "corridor_code": "MULTI_HOP_EURO_02",
      "path_nodes": [
        {"step": 1, "bic": "USBAUS33XXX", "role": "Debtor Agent", "name": "Bank B1 (USA)"},
        {"step": 2, "bic": "EURBDEFFXXX", "role": "Intermediary 1", "name": "Bank E1 (Europe)"},
        {"step": 3, "bic": "INDBINBBXXX", "role": "Intermediary 2", "name": "Bank I1 (India)"},
        {"step": 4, "bic": "SBININBBXXX", "role": "Creditor Agent", "name": "State Bank of India"}
      ],
      "estimated_metrics": {
        "turnaround_time_minutes": 380,
        "estimated_completion_time": "2026-10-04T06:35:00Z",
        "fx_rate_applied": 83.58,
        "gross_fx_amount_target": 8358000.00,
        "lifting_fees_usd": 12.00,
        "fx_margin_cost_usd": 8.00,
        "total_cost_usd": 20.00
      },
      "confidence_score": 0.88,
      "tradeoff_summary": "2-hop correspondent route. Significantly cheaper, but incurs time-zone delay at E1 cut-off window."
    }
  ]
}