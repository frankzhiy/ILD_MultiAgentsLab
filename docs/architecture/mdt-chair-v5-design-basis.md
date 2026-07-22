# MDT chair v5: design-time clinical basis

This note records the medical sources used to design the chair's prompts and data contract. It is not loaded, retrieved, or cited by the chair at runtime.

## Authoritative sources

1. **Chinese Thoracic Society ILD-MDD consensus (2023)**  
   Local source: `data/guidelines/cts_ild-mdd_consensus_2023_zh.pdf`
   - PDF pp. 6-7: MDD quality depends on standardized, sufficiently complete clinical information and high-quality imaging/pathology material; diagnoses should carry confidence, and low-confidence cases can require further evaluation or remain unclassifiable.
   - PDF p. 7: disagreement should lower diagnostic confidence; missing examinations and information should be stated explicitly for later reassessment.
   - Design consequence: an absent or inadequate source is an assessment boundary/evidence need, not a positive specialty conclusion and not a negative finding.

2. **ERS/ATS international multidisciplinary classification of interstitial pneumonias (2025)**  
   Local source: `data/guidelines/ers-ats_iip-classification_statement_2025.pdf`
   - PDF p. 3: a radiologic or histologic pattern is distinct from a multidisciplinary disease diagnosis.
   - PDF pp. 8-9: diagnostic confidence should be documented after integration of clinical, radiologic, and laboratory information; additional pathology may be considered when a high-confidence clinical-radiologic diagnosis is unavailable.
   - Design consequence: propositions are compared only at the same object, time, evidence scope, and professional level. A lower-level pattern statement cannot automatically confirm or contradict a disease-level attribution.

3. **ATS/ERS/JRS/ALAT IPF/PPF clinical practice guideline (2022)**  
   Local source: `data/guidelines/ats-ers-jrs-alat_ipf-ppf_cpg_2022.pdf`
   - PDF pp. 11-13: HRCT categories explicitly represent different confidence levels; the guideline prefers an `indeterminate` label over a more limiting negative label for heterogeneous combinations, with diagnostic interpretation occurring in MDD.
   - Design consequence: `possible`, `indeterminate`, and `not_assessable` must not be transformed into direct denial. They therefore cannot supply either side of a true contradiction.

## Derived chair rules

The sources above support preserving professional level, diagnostic confidence, and missing-data boundaries. The following contradiction test is a conservative logical implementation of those constraints, not a quotation from a guideline:

- A true cross-specialty conflict requires the same atomic proposition, object, timeframe, evidence conditions, and professional level, with one formal conclusion directly affirming it and another directly denying it.
- `Possible`, `indeterminate`, `not assessable`, `not applicable`, and statements that only report missing material are excluded from both conflict positions.
- A specialty response and a resolved question are separate states. A response that only states an evidence boundary counts as a response but does not by itself resolve the underlying question.
- Evidence coverage is determined from the information actually supplied by cited conclusions, not from the fact that a specialty spoke or was cited.

These rules are encoded in the two-stage semantic-ledger and synthesis prompts. Runtime receives only the four formal specialty-output projections and program-created source references; guideline text is intentionally excluded.
