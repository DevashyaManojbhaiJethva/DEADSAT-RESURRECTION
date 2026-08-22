# Model Card — AI-1 Satellite Fault Classifier

> **This file is generated.** It is written by `write_model_card()` at the
> end of every training run. Do not edit it by hand — rerun training:
> ```
> python train_classifier.py --csv data/synthetic_orbital_series.csv
> ```
> Generated 2026-08-18 05:29 UTC · seed 42

## Model

| | |
|---|---|
| Task | 4-class satellite fault classification from orbital elements |
| Architecture | Transformer encoder, 2 layers, d_model=64, 4 heads |
| Input | 8 consecutive epochs × 11 features |
| Classes | SEU, SOFTWARE_BUG, FIRMWARE_CORRUPTION, COMMAND_INJECTION |
| Anomaly gate | Isolation Forest, fitted on NORMAL training rows only |

## Dataset provenance

Real orbital elements, synthetic faults. Specifically:

- **Source.** CelesTrak-format GP element sets for **712 satellites**
  (`data/input.csv`, `input__1_.csv`, `input__2_.csv`), deduplicated on
  `(NORAD_CAT_ID, EPOCH)`.
- **Propagation.** Each entry is propagated forward with SGP4 via
  `satellite_catalog.build_tle_from_gp()`, one epoch per revolution, and
  osculating elements are re-derived at each step (`generate_dataset.py`).
- **Fault injection.** Signatures are stamped onto the resulting *series*
  to match `assign_fault_labels()` exactly, including its precedence order.

**Why synthetic faults?** Labelled satellite fault telemetry is not
publicly available. Operators do not publish telemetry from anomalous
spacecraft, and the fault taxonomy this project targets (SEU, software
bug, firmware corruption, command injection) has no open labelled corpus
at all. The orbital dynamics here are real; the faults are not. That
distinction is the single most important limitation of this model and is
expanded on below.

The original snapshot could not be used directly: it held one epoch per
satellite, so `REV_DELTA` and `ecc_delta` were zero for 100% of rows and
three of the four label rules — all defined as changes *between* epochs —
could never fire. 807 of 854 rows collapsed to SOFTWARE_BUG.

## Split strategy

`GroupShuffleSplit(groups=NORAD_CAT_ID)`, two-stage, so **every satellite
lands wholly in exactly one of train / val / test**.

Consecutive epochs of one satellite are near-identical by construction.
A random *row* split therefore puts almost-duplicate rows on both sides of
the boundary, and the model can memorise a satellite in training and
recognise it at test time. Grouping by satellite is what makes the test
score an estimate of performance on *unseen spacecraft* rather than on
unseen rows of familiar ones.

| Split | Satellites | Windows |
|---|---:|---:|
| train | 481 | 1600 |
| val | 69 | 376 |
| test | 138 | 750 |

Two further orderings matter and are enforced in `build_dataloaders()`:

- Augmentation runs on the **training split only**. It oversamples with
  replacement and adds noise of 0.05 × class std, i.e. near-duplicates;
  running it before the split put copies of the same rows in train, val
  and test.
- The `StandardScaler` is fitted on the **training split only**, then
  applied to val and test.

- Windows never span two satellites: sequences are built within each
  `NORAD_CAT_ID`, sorted by `EPOCH`.

## Results — held-out test split

Test set: **750 windows** from satellites never seen
in training.

### Baseline comparison

All models consume the identical leak-free windows; the baselines see them
flattened to 8×11 features. The only
difference is the model.

| Model | Accuracy | Macro F1 | Weighted F1 |
|---|---:|---:|---:|
| Transformer encoder | 98.53% | 0.9757 | 0.9856 |
| Logistic regression | 97.47% | 0.9588 | 0.9756 |
| Gradient-boosted trees **← best** | 99.20% | 0.9862 | 0.9921 |
| Majority class (floor) | 9.20% | 0.0421 | 0.0155 |

### Verdict

**The transformer does NOT win.** Gradient-boosted trees scores higher by
**+0.0105** macro F1.

This is reported rather than buried. A simpler model outperforming the
transformer on this data is a legitimate finding, and a more credible
one than an unsupported claim to the contrary. It suggests the label
rules are largely threshold-based on individual rows, which is exactly
what a tree ensemble captures well and what a sequence model cannot
improve on. Options: adopt the simpler model, or demonstrate that the
sequence structure carries signal the thresholds miss.

### Per-class — transformer

| Class | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| SEU | 0.873 | 1.000 | 0.932 | 69 |
| SOFTWARE_BUG | 0.990 | 1.000 | 0.995 | 103 |
| FIRMWARE_CORRUPTION | 1.000 | 0.952 | 0.975 | 227 |
| COMMAND_INJECTION | 1.000 | 1.000 | 1.000 | 351 |

### Confusion matrix — transformer

Rows = true, columns = predicted.

| | SEU | SOFTWARE_BUG | FIRMWARE_CORRUPTION | COMMAND_INJECTION |
|---|---:|---:|---:|---:|
| **SEU** | 69 | 0 | 0 | 0 |
| **SOFTWARE_BUG** | 0 | 103 | 0 | 0 |
| **FIRMWARE_CORRUPTION** | 10 | 1 | 216 | 0 |
| **COMMAND_INJECTION** | 0 | 0 | 0 | 351 |

## Limitations

**1. There is no real fault-labelled telemetry in this dataset, and none
was available to build one.** Every fault signature was injected by
`generate_dataset.py` to match the thresholds in `assign_fault_labels()`.
The model is therefore measured on its ability to recover a rule set that
is known in advance. A high score demonstrates that the pipeline is
internally consistent and free of the leaks it was audited for. **It does
not demonstrate that the model would detect a real SEU on a real
spacecraft**, and no claim to that effect should be made from these
numbers.

**2. The labels are heuristics, not ground truth.** `assign_fault_labels()`
maps orbital-element symptoms onto fault categories using fixed
thresholds. Those mappings are plausible but unvalidated against real
anomaly reports. If a threshold is wrong, the model faithfully learns the
wrong thing and the test score stays high.

**3. A first-row artefact inflates SOFTWARE_BUG.** `prepare_dataframe()`
computes `REV_DELTA` with `.diff().fillna(0)`, so the first row of every
satellite is forced to zero and rule 5 labels it SOFTWARE_BUG. That is
exactly `1 / n_epochs` of the dataset — mislabelled by construction.

**4. Generalisation is bounded by the catalogue.** All 712 satellites come
from three overlapping CelesTrak exports dominated by LEO; ~97% have
periods under 9 hours. Satellites whose orbits are too slow for the
staleness window are excluded outright. Nothing here says anything about
MEO, GEO or highly elliptical regimes.

**5. Class balance is a design choice, not a prior.** Allocation across
the four classes is set by the generator, so the class distribution
reflects that choice and carries no information about real fault rates.

**6. Single seed.** These figures are one run at seed
42. No variance across seeds is reported, so small
margins between models should not be treated as meaningful.

## Reproducing every number above

```bash
pip install -r requirements.txt
python generate_dataset.py --propagator sgp4 --verify
python train_classifier.py --csv data/synthetic_orbital_series.csv
```
The last command rewrites this file. Metrics come from
`evaluate_model()` and `run_baselines()`; the prose comes from
`write_model_card()` in `models/satellite_fault_classifier_V2.py`.
