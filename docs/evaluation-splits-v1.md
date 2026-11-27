# CSI evaluation splits (v1)

**Jira:** GES-74. Uses the session contract docs/csi-session-contract-v1.md (schema 1.0.0).

This protocol defines how recorded sessions are divided for evaluation so that no information from test sessions reaches training. It is agreed before real data collection starts. Implementing the split code (UC22) is later work.

**Sources:**

- The session contract, [docs/csi-session-contract-v1.md](csi-session-contract-v1.md), Section 3 (session layout, metadata and evaluation groups).
- The recording procedure (GES-73), Confluence page [Recording Procedure and Consent Form](https://gestura-se4910.atlassian.net/wiki/spaces/GP/pages/23101469/Recording+Procedure+and+Consent+Form), Sections 4 and 7.
- The Statement of Work: Table 11 (NFR-01 to NFR-04) and Table 12 (their targets).

## 1. Units and what is left out

- **The session is never divided.** A session is one participant in one room on one day. All windows and trials of a session stay on the same side of a split.
- **Groups are held out whole.** When a group (a day, a participant or a room) is held out, every session of that group is held out together.
- **Failed trials.** Trials with `failed = true` in `trials.csv` stay in the raw data but are excluded from training, validation and testing.
- **Final demonstration room.** Sessions with `room_role: final_demo` are never used for training or validation. They are used only for the final test and for rehearsal.
- **Continuous recordings.** Sessions with `session_type: continuous_no_gesture` are not part of the classification splits. They are used only to measure unintended triggers (NFR-05) and never for training.

## 2. The three comparisons

The targets below are provisional acceptance targets from the SoW (Table 12). They are not measured results.

### 2.1 Held-out day

- **Grouping key:** `recording_date`.
- **Rule.** The SoW (NFR-01) says to train on the first two recording days of each room and test on the third. A room's day order comes from sorting the `recording_date` values of its sessions; the recording procedure records each room on 3 different days. The contract (Section 3) says that every session of a held-out date, in any room, is excluded from training. Both rules are combined:
  - **Test:** the sessions on each room's third day. Their dates are the test dates.
  - **Training and validation:** the sessions on each room's first and second days, except any session whose `recording_date` equals a test date (in any room). Such sessions are used for neither training nor validation.
- **Note on the recording schedule.** The schedule should avoid a date being one room's third day and another room's first or second day. Under the combined rule, the sessions of that other room on that date would drop out of training.
- **Target:** macro-F1 ≥ 0.70; lowest per-class F1 ≥ 0.40 (NFR-02).

### 2.2 Held-out participant

- **Grouping key:** `participant_code` (`P1`–`P5`).
- **Rule.** Leave one participant out: train on the sessions of the other four participants and test on the sessions of the held-out participant. This gives 5 folds.
- **Target:** macro-F1 ≥ 0.60.

### 2.3 Held-out room

- **Grouping key:** `room_id`.
- **Rule.** Leave one dataset room out: train on the sessions of the other dataset rooms and test on the sessions of the held-out room. This gives one fold per dataset room. The final demonstration room is never in training (Section 1).
- **Target:** macro-F1 ≥ 0.50.

### 2.4 Reporting the comparisons

Each comparison is reported separately. The results of the three comparisons are never averaged into one number.

## 3. Validation and model selection

**Validation sessions.** Validation sessions come only from the training side of each split. They are whole sessions, grouped by the same key as the split:

| Comparison | Validation sessions |
|---|---|
| Held-out participant | All sessions of one training participant. |
| Held-out room | All sessions of one training room. |
| Held-out day | The sessions on the second day of each room. |

> **For the reviewers to confirm:** this validation grouping is a choice made in this protocol, not a rule from the SoW or the contract.

**What is decided without test data.** Everything that is chosen or fitted is decided on training and validation data only, never on test data:

- the recognizer;
- its settings;
- the confidence threshold (SoW Table 11);
- feature normalization statistics;
- the threshold reference used for NFR-03.

**Retraining and testing.** After selection, the model may be retrained on all training sessions of the split with the chosen settings. The test sessions are used once, for the reported result.

## Open questions

1. **Held-out day:** is the combined rule of Section 2.1 acceptable, and will the recording schedule avoid the date overlap described there?
2. **Held-out room:** the recording procedure (GES-73, Section 4) says data is collected in 7 rooms and that the final demonstration room is kept out of training. Is the final demonstration room one of these 7 rooms (6 dataset rooms, 6 folds) or an additional room (7 dataset rooms, 7 folds)?
3. **Held-out day:** a room's second day (validation) can fall on the same date as another room's first day (training), which puts validation and training sessions on the same date. Should such training sessions be left out of the inner training set during model selection, as is done for the test dates?
