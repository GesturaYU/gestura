# CSI evaluation splits (v1)

**Jira:** GES-74. Uses the session contract docs/csi-session-contract-v1.md (schema 1.0.0).

This protocol defines how recorded sessions are divided for evaluation so that no information from test sessions reaches training. It is agreed before real data collection starts. Implementing the split code (UC22) is later work.

**Sources:**

- The session contract, [docs/csi-session-contract-v1.md](csi-session-contract-v1.md), Sections 2 and 3 (host records; session layout, metadata and evaluation groups).
- The recording procedure (GES-73), Confluence page [Recording Procedure and Consent Form](https://gestura-se4910.atlassian.net/wiki/spaces/GP/pages/23101469/Recording+Procedure+and+Consent+Form), Sections 4 and 7.
- The Statement of Work: Table 11 (NFR-01 to NFR-05) and Table 12 (their targets).

## 1. Units and what is left out

- **The session is never divided.** A session is one participant in one room on one day. All windows and trials of a session stay on the same side of a split.
- **Groups are held out whole.** When a group (a day, a participant or a room) is held out, every session of that group is held out together.
- **Failed trials.** Trials with `failed = true` in `trials.csv` stay in the raw data but are excluded from training, validation and testing.
- **Final demonstration room.** Sessions with `room_role: final_demo` are never used for training or validation. They are used only for the final test and for rehearsal.
- **Continuous recordings.** Sessions with `session_type: continuous_no_gesture` are not part of the classification splits. They are used only to measure unintended triggers (NFR-05) and never for training.

## 2. The three comparisons

The three comparisons use only sessions with `room_role: dataset` and `session_type: cued_trials`; `final_demo` sessions are kept for the final test (Section 1).

The targets below are provisional acceptance targets from the SoW (Table 12). They are not measured results. On each comparison, the selected recognizer must also show a positive difference in macro-F1 over the threshold reference (NFR-03).

### 2.1 Held-out day

- **Grouping key:** `recording_date`.
- **Rule.** The SoW (NFR-01) says to train on the first two recording days of each room and test on the third. A room's day order comes from sorting the `recording_date` values of its sessions; the recording procedure records each room on 3 different days. The contract (Section 3) says that every session of a held-out date, in any room, is excluded from training. Both rules are combined:
  - **Test:** the sessions on each dataset room's third day. Their dates are the test dates.
  - **Training and validation:** the sessions on each dataset room's first and second days, except any session whose `recording_date` equals a test date (in any room). Such sessions are used for neither training nor validation.
- **Note on the recording schedule.** The schedule should avoid a date being one room's third day and another room's first or second day. Under the combined rule, the sessions of that other room on that date would drop out of training.
- **Target:** macro-F1 ≥ 0.70; lowest per-class F1 ≥ 0.40 (NFR-02).

### 2.2 Held-out participant

- **Grouping key:** `participant_code` (`P1`–`P5`).
- **Rule.** Leave one participant out: train on the sessions of the other four participants and test on the sessions of the held-out participant. This gives 5 folds.
- **Target:** macro-F1 ≥ 0.60.

### 2.3 Held-out room

- **Grouping key:** `room_id`.
- **Rule.** Leave one dataset room out: train on the sessions of the other dataset rooms and test on the sessions of the held-out room. This gives one fold per dataset room (see open question 2). The final demonstration room is never in training (Section 1).
- **Target:** macro-F1 ≥ 0.50.

### 2.4 Reporting the comparisons

Each comparison is reported separately. The results of the three comparisons are never averaged into one number.

## 3. Validation and model selection

**Validation sessions.** Validation sessions come only from the training side of each split. They are whole sessions, grouped by the same key as the split:

| Comparison | Validation sessions |
|---|---|
| Held-out participant | All sessions of one training participant. |
| Held-out room | All sessions of one training room. |
| Held-out day | The sessions on the second day of each dataset room (see open question 3). |

> **For the reviewers to confirm:** this validation grouping is a choice made in this protocol, not a rule from the SoW or the contract.

**What is decided without test data.** Everything that is chosen or fitted is decided on training and validation data only, never on test data:

- the recognizer;
- its settings;
- the confidence threshold (SoW Table 11);
- feature normalization statistics;
- the threshold reference used for NFR-03.

**Retraining and testing.** After selection, the model may be retrained on all training sessions of the split with the chosen settings. The validation sessions then count as training sessions. The test sessions are used once, for the reported result.

## 4. Required metadata

**Fields.** The splits in Sections 1–3 need the following fields. The storage locations are those of the session contract (Sections 2 and 3).

| Field | Where the contract stores it | Used for |
|---|---|---|
| `session_id` | `session.json` and `trials.csv`. | Keeps a session whole; joins trials to their session. |
| `recording_date` | `session.json` (local Europe/Istanbul date of the session start; also the directory prefix). | Held-out day grouping and each room's day order. |
| `participant_code` | `session.json`. | Held-out participant grouping. |
| `room_id` | `session.json`. | Held-out room grouping; each room's day order. |
| `room_role` | `session.json`. | Keeps `final_demo` sessions out of training and validation. |
| `session_type` | `session.json`. | Keeps `continuous_no_gesture` sessions out of the classification splits. |
| `class_label` | `trials.csv`. | Labels; per-class sample counts (NFR-04). |
| `failed` | `trials.csv`. | Excludes failed trials. |
| `cue_start_monotonic_ns`, `cue_end_monotonic_ns` | `trials.csv`; matched to `host_monotonic_ns` in `receivers/<receiver_id>.ndjson`. | Assigns packets and windows to trials. |
| `schema_version` | `session.json`. | All sessions in one evaluation use the same schema version. |

**Fields per comparison.**

| Comparison | Fields needed |
|---|---|
| All comparisons | `session_id`, `room_role`, `session_type`, `class_label`, `failed`, `cue_start_monotonic_ns`, `cue_end_monotonic_ns`, `schema_version` |
| Held-out day | `recording_date`, `room_id` (each room's day order) |
| Held-out participant | `participant_code` |
| Held-out room | `room_id` |

**Schema check.** Every field above is stored by contract 1.0.0 (Sections 2 and 3), so no field is missing for the splits as defined in Sections 1–3. One possible gap remains: `session.json` records whether the recorder was in the room (`recorder_in_room`) but not who the recorder was. The recorder is one of P1–P5, so in the held-out participant comparison the held-out participant may have been in the room as the recorder during training sessions, and the protocol cannot detect this. See open question 4.

## 5. Leakage check (later work)

The split code must run these checks for every fold before any result is reported. If a check fails, no result of that fold is reported.

1. No `session_id` is in more than one of inner training, validation and test during model selection, and no test session is in the final training set.
2. No value of the held-out key (test date, held-out participant code, held-out room ID) appears in the training or validation sessions. For held-out day, this includes the date rule of Section 2.1.
3. No `final_demo` session is in training or validation.
4. No `continuous_no_gesture` session is in any classification split.
5. No trial with `failed = true` is in training, validation or test.
6. All sessions used in the evaluation have the same `schema_version`.
7. Everything that is fitted or chosen (Section 3) records the `session_id`s it was computed from, and none of them is a test session.

## 6. What is reported with each result

| Field | Content |
|---|---|
| Comparison and fold | Held-out day, held-out participant or held-out room, and the fold. |
| Groups | The held-out group (test dates, participant code or room ID) and the validation group. |
| Sessions | The `session_id`s of training, validation and test. |
| Sample counts | Accepted samples per class (failed trials excluded) for the whole dataset and in training, validation and test (NFR-04). Samples are counted from the trial labels in `trials.csv`, as NFR-04 counts them from the dataset labels. |
| Metric definition | Macro-averaged F1 as the unweighted mean of the per-class F1 over the four classes `push`, `slow_wave`, `fast_wave` and `no_gesture` (NFR-01). Per-class precision, recall and F1 from the confusion matrix, and the lowest per-class F1 (NFR-02). The difference in macro-averaged F1 between the selected recognizer and the threshold reference, on the same test sessions (NFR-03). The 95% confidence interval from resampling complete test sessions (NFR-01); for NFR-03, the 95% confidence interval of the paired difference (Table 12) (see open question 5). |
| Versions | `schema_version` and the commit of the evaluation code, so that the result can be reproduced. |

## 7. Later work

- Implementing the split code (UC22) and the leakage check of Section 5 is later work, outside GES-74.
- Model performance is not reported in GES-74.

## Open questions

1. **Held-out day:** is the combined rule of Section 2.1 acceptable, and will the recording schedule avoid the date overlap described there?
2. **Held-out room:** the recording procedure (GES-73, Section 4) says data is collected in 7 rooms and that the final demonstration room is kept out of training. Is the final demonstration room one of these 7 rooms (6 dataset rooms, 6 folds) or an additional room (7 dataset rooms, 7 folds)?
3. **Held-out day:** a room's second day (validation) can fall on the same date as another room's first day (training), which puts validation and training sessions on the same date. Should such training sessions be left out of the inner training set during model selection, as is done for the test dates?
4. **Recorder identity (held-out participant):** the person who records a session is one of P1–P5, but `session.json` stores only whether the recorder was in the room (`recorder_in_room`), not who it was. The held-out participant may therefore have been in the room as the recorder during training sessions, which the protocol cannot detect. Adding the recorder's participant code would be a contract change (GES-67). Should it be added? This must be decided before real collection.
5. **Combining folds:** the held-out participant and held-out room comparisons have several folds, but each has one SoW target. Is the reported value computed from the pooled test predictions of all folds, or as the mean of the fold results, and how is the confidence interval resampled in that case?
