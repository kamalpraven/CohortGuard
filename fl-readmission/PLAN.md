# Phase 4 plan: federated 30-day readmission model

Status: **plan for review**. Nothing below is built yet.

## Goal

Train one 30-day readmission-risk model across Clinic A and Clinic B where each clinic trains on its own records and only model weights (plus a few aggregate metrics) leave. Show whether it recovers the direction of the planted SGLT2 effect: `generate_data.py` lowers readmission probability by 0.07 for current `sglt2_inhibitor` users. The raw data already shows it at both sites: A 12.5% vs 16.3% for non-users (n=112 users), B 11.1% vs 22.0% (n=117).

## What Flower 1.39 gives us (read from the installed package and the Hub templates)

- `flwr new` now fetches templates from Flower Hub (`flwr new @flwrlabs/quickstart-sklearn`, `@flwrlabs/quickstart-numpy`). There is no `--framework` flag any more.
- Message API: `ServerApp` with `@app.main() def main(grid: Grid, context: Context)`, and `ClientApp` with `@app.train()` / `@app.evaluate()` handlers taking `(msg: Message, context: Context)`. They return `Message(RecordDict({"arrays": ArrayRecord, "metrics": MetricRecord}), reply_to=msg)`.
- `FedAvg(fraction_train, fraction_evaluate, min_*_nodes, weighted_by_key="num-examples")`. `strategy.start(grid, initial_arrays, num_rounds, train_config, evaluate_config, evaluate_fn)` returns per-round aggregated client metrics. MetricRecords are aggregated as a weighted mean by `num-examples`.
- **Node selection:** every FedAvg round calls `sample_nodes(grid, ...)`, which uses `grid.get_node_ids()`. So the node set is decided entirely by what the ServerApp's `Grid` reports.
- DP: `DifferentialPrivacyClientSideFixedClipping(strategy, noise_multiplier, clipping_norm, num_sampled_clients, accountant=...)` on the server, plus `fixedclipping_mod` on the ClientApp. 1.39 also ships an RDP `PrivacyAccountant`.
- Simulation: the `local` connection (`address = ":local:"`) with `options.num-supernodes`. In simulation each node's `node_config` has `partition-id` and `num-partitions`.
- **Windows constraint:** `flwr[simulation]` resolves *without* Ray on Python 3.13 on Windows. It gets `ray==2.55.1` on 3.12. So `fl-readmission/` gets its own uv venv pinned to Python 3.12 for simulation. Deployment SuperNodes keep using the coordinator's 3.13 environment, which is fine because deployment needs no Ray.

## Proposal

### 1. Framework: NumPy logistic regression (recommended over scikit-learn)

- Plain NumPy: full-batch gradient descent on L2-regularized log loss, with a fixed number of local epochs per round. Weights are one vector of 22 floats: 21 coefficients plus an intercept.
- **Why not scikit-learn:** the template's `LogisticRegression(warm_start=True, max_iter=1, solver="saga")` trick is non-deterministic across runs and raises convergence warnings. It would also make every SuperNode install scikit-learn at runtime. With NumPy:
  - federated, clinic-only and pooled models use the *same* trainer, so the comparison is fair;
  - results are exactly reproducible;
  - the FAB depends only on `numpy`.
- AUC uses the rank-based (Mann–Whitney) formula in NumPy. Tests will cross-check it against scikit-learn, which is a dev-only dependency.

### 2. Features and label (clinic_core record fields; never `mrn`, `name`, `dob`)

The loader builds features from an explicit allowlist of keys and never reads `mrn`, `name` or `dob`. It fails if a record lacks any required key.

| Feature | Encoding (fixed clinical constants, never dataset statistics) |
|---|---|
| age | (age − 60) / 20 |
| sex | 1 if FEMALE else 0 |
| hba1c | (hba1c − 8) / 2 |
| egfr | (egfr − 80) / 25 |
| bmi | (bmi − 30) / 6 |
| diagnosis | multi-hot over `DIAGNOSIS_CODES` (4) |
| current medication | multi-hot over `MEDICATION_CLASSES` (6) |
| prior medication | multi-hot over `MEDICATION_CLASSES` (6) |

Label: `readmitted_30d`. The code orders categories by the sorted allowlist, so the feature order is identical at every clinic.

*Decision for you:* I propose **centered** constants like those above rather than plain `age/100`, `hba1c/15`. Both are fixed constants and share nothing. Centering makes gradient descent converge in far fewer rounds and keeps the intercept interpretable. If you prefer the plain divisors, it's a one-line change.

### 3. Local split

Each clinic makes a stratified 20% test split on the label, with a fixed seed from run config (`split-seed`, default 20260930). The shuffle comes from `numpy.random.default_rng(split_seed)`, applied separately per class, over records sorted by an internal row index (never by MRN). Test sets are about A: 105 and B: 129 patients.

### 4. How each ClientApp finds its data

- **Deployment:** `data-path` from `node_config`, exactly as the clinic SuperNodes are already started, plus `role` (`clinic_a` / `clinic_b`). As in `coordinator/nodes.py`, the filename must match the role (`clinic_a_patients.json` for `clinic_a`). The path only ever comes from `node_config`, never from run config, so the server cannot point a clinic at another file.
- **Simulation:** `node_config` has no `data-path` but has `partition-id`. Partition 0 maps to `clinic_a` and 1 to `clinic_b`, under a directory given by run config `sim-data-dir` (default empty, which is an error). This path is honoured only when `node_config` has `partition-id` and no `data-path`, so a deployment node can never be redirected.
- No patient JSON goes in the FAB (`fab-include` limited to code, README and LICENSE). A test asserts this on the built FAB.

### 5. The ServerApp selects only clinic nodes (research node never gets a task)

Three layers:

1. **Pinned node IDs.** Run config `clinic-node-ids = '{"clinic_a":"…","clinic_b":"…"}'`, same format as the coordinator's `expected_role_node_ids`. The ServerApp wraps its `Grid` in a `ClinicOnlyGrid`:
   - `get_node_ids()` returns only the pinned IDs, so FedAvg can only sample them;
   - `push_messages` / `send_and_receive` refuse any message whose destination isn't pinned (fail closed);
   - the run fails if a pinned node isn't connected, rather than silently training on fewer clinics.

   The server never sends anything, not even a role query, to an unpinned node.
2. **Role check in replies.** Each train/evaluate reply carries a `ConfigRecord {"role": "clinic_a"}`. The server checks it against the pinned mapping every round and aborts on a mismatch.
3. **ClientApp self-check.** A node whose `role` is not a clinic, such as the research node, refuses and returns no arrays.

**Simulation:** node IDs are random, so they can't be pinned in advance. `clinic-node-ids = "{}"` is accepted only when run config `simulation = true`. In that mode the server requires exactly two nodes and relies on layers 2 and 3. Deployment requires the pinned mapping.

**Local deployment:** get IDs from each SuperNode's startup log line (`SuperNode ID: …` in `runtime-logs/clinic_*.err.log`). I'll start the **research SuperNode too**, then show from its log that it received no FL message.

### 6. Strategy and training config

FedAvg with `fraction_train = fraction_evaluate = 1.0` and `min_*_nodes = 2`, run for 20 rounds. Every round each clinic runs 5 local full-batch epochs (lr 0.5, L2 1e-3), and every round is evaluated on each clinic's local test set. These are run-config defaults; I'll tune them once in simulation and then freeze them before any reported run.

Each round logs the weighted AUC and log loss (weighted mean over clinics by test `num-examples`). A mean of per-clinic AUCs is not a pooled AUC, and I'll label it that way.

### 7. Getting results out (works for simulation, local and SuperGrid)

The ServerApp logs one final `FL_RESULT {json}` line: per-round metrics, final global weights and feature names. It does not write files, because on SuperGrid the ServerApp runs remotely. `fl-readmission/run_experiment.py` streams the run, parses that line and writes `results/fl_results.json`. It then runs the offline comparison and the plots.

### 8. Evaluation (clearly labeled)

- **Federated:** final FedAvg weights.
- **Clinic-only:** each clinic trains alone with the same trainer and total epochs (20 × 5).
- **Pooled (upper bound):** trained on the union of both train sets. This is labeled as possible only because the data is synthetic.
- All models are scored on A-test, B-test and the combined test set, reporting AUC and log loss. A second table ranks the scenarios (federated vs clinic-only vs pooled).
- **SGLT2:** the federated `sglt2_inhibitor` coefficient and odds ratio exp(β), adjusted for the other features, with its sign.
  - The pooled model's coefficient gets a Wald 95% CI from its Hessian, as a reference.
  - Each clinic-only coefficient is shown too.
  - The write-up says whether the coefficient is negative as planted. The planted effect is additive on the probability scale (−0.07), so an odds ratio is not the planted size and I won't claim it is. I'll report direction and magnitude only.

### 9. Privacy

- **Canary scan:** a server-side reply validator records every reply's record structure (record types, keys, array shapes, metric keys) into the result. It aborts on anything outside a fixed allowlist: `arrays` (22 floats), `metrics` {`num-examples`, `train-loss`, `test-loss`, `auc`}, and `config` {`role`}. The scan covers:
  - that message log;
  - the streamed Flower run events and logs;
  - `runtime-logs/*.log` (SuperLink plus all three SuperNodes);
  - `fl_results.json`.

  It looks for every planted canary's name, MRN and DOB formats, plus the record keys `mrn`/`name`/`dob`, and must print `canary hits: 0`.
- **Known limits to document (please read):**
  - **Gradients reveal aggregates.** With a zero initial model, a clinic's first-round update is roughly its per-feature sums of (y − ½)·x. For a binary feature such as `sglt2_inhibitor`, that is close to "events − n/2 among SGLT2 users" at that clinic: **un-noised and without the min-cell rule.** So FL without DP releases the same *kind* of aggregate the privacy gate protects, including for small subgroups (the planted rare cases). The model is small (22 parameters) and the features coarse, so reconstructing individual records is unlikely with n ≫ d. The risk is still not zero, and the per-subgroup aggregate leak is real.
  - **The server sees each clinic's update individually.** There is no secure aggregation. With only two clinics, secure aggregation would help little: each clinic could subtract its own update from the sum.
  - Per-clinic test metrics (AUC, log loss) and exact `num-examples` also leave each clinic.
  - FL releases are **not charged to the clinic privacy ledgers** (question below).
- **Stretch (only once the core works):** Flower client-side fixed clipping with noise multipliers of about {0.1, 0.5, 1.0}, reporting AUC and the accountant's ε for each. **Caveat:** this is *client-level* DP, meaning it protects a whole clinic's contribution. With two clients the noise needed for a meaningful ε will probably wreck utility, and it is not the patient-level guarantee the gate gives. Patient-level DP would need DP-SGD inside each clinic (per-record clipping plus noise). I'd list that as future work, not build it now.

### 10. Run sequence

1. Simulation: `flwr run fl-readmission local --stream` with `num-supernodes = 2`, `simulation=true`, `sim-data-dir=…`.
2. Local deployment: start the local grid with a fresh `--session` (SuperLink and all three SuperNodes), read the clinic node IDs from the logs, then `flwr run fl-readmission local-agent --stream` with `clinic-node-ids` pinned. Then run the canary scan, including the research node's log showing zero FL messages.
3. **Stop and ask you before SuperGrid.** It's a ServerApp run with no model calls, but it's still your call.

### 11. Outputs and tests

- `fl-readmission/` is its own Flower app (ServerApp plus ClientApp; it can't share the AgentApp bundle). Publisher `praven1`, package `fl_readmission` (`task.py`, `client_app.py`, `server_app.py`, `selection.py`, `evaluate.py`). It also holds `run_experiment.py`, a `pyproject.toml` (deps `flwr>=1.39,<2`, `numpy`; dev deps `pytest`, `scikit-learn`, `matplotlib`, `flwr[simulation]`) and `.python-version` 3.12.
- `fl-readmission/results/fl_results.json`, `docs/media/fl-readmission-auc-per-round.png` and `docs/media/fl-readmission-comparison.png`.
- `fl-readmission/README.md` with commands and a results table.
- Tests:
  - loader never emits identifier fields, and a record carrying extra identifier keys yields the same features;
  - fixed scaling is independent of the dataset (scaling one record alone equals scaling it inside the full set);
  - stratified split is reproducible and keeps class ratios;
  - `ClinicOnlyGrid` excludes the research node, refuses unpinned destinations and fails on a missing pinned node;
  - ClientApp refuses a research role, a wrong data-path filename and a run-config path in deployment;
  - reply validator rejects unexpected keys;
  - NumPy AUC matches scikit-learn;
  - results pipeline builds the JSON schema and both plots from a small fixture run;
  - the FAB contains no patient JSON.
- Commits in small steps on `phase-4-federated`: scaffold → task/data → selection → apps → simulation run → evaluation/plots → local deployment + scan → docs. Then merge to `main` locally with `--no-ff`, and nothing gets pushed.

## Questions for you

1. NumPy logistic regression instead of scikit-learn: OK?
2. Centered fixed constants (section 2) instead of plain divisors: OK?
3. The FL run releases un-noised, per-clinic aggregate-like updates (section 9). Accept that for Phase 4 and document it? Or should FL runs also require or consume clinic ledger budget, or be gated on the DP stretch?
4. The stretch DP is clinic-level only. Still want it as specified, or skip it in favour of noting patient-level DP-SGD as future work?
5. Local deployment will run the research SuperNode alongside the clinics, to show it receives nothing. OK?
