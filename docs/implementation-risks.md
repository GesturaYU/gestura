# Implementation risks and technical-item mapping

Jira: GES-76. State on 28 November 2026 (end of Sprint 4).

This page holds the implementation risk register and a draft mapping of the Sprint 4 stories to technical items.
The components and their data interfaces are described in [`architecture-overview.md`](architecture-overview.md).

## 1. Risk register

Likelihood and impact are rated Low, Medium or High. The register is reviewed at each sprint planning, and a new
risk gets the next free ID. **Owners** acknowledge their entries with a comment on the pull request that adds or
changes them, for example "R1, R5 acknowledged".

| ID | Risk | Likelihood | Impact | Mitigation | Owner | Status |
|---|---|---|---|---|---|---|
| R1 | **Board access.** The boards are shared and partly borrowed, and there is no fixed plan for who holds which board. One borrowed sender sent no packets (GES-75). No second receiver board has been tested. Without boards, the two-receiver check and every recording session stop. | High | High | Agree in Sprint 5 planning who holds which board (B1, B2, …) and when. Check the silent board and, if it works, flash it as the second receiver. Decide whether to get a spare ESP32-S3 board. | Efecan Cengiz | Open |
| R2 | **Packet reliability over full sessions.** NFR-09 (at least 80 packets/s, loss below 5 %) was met only in short captures of 60–300 s with one receiver (87–91 packets/s, 0–4 % loss). None of these has been measured yet: a 20-minute session, two receivers on one laptop, other rooms. | Medium | High | Run a signal check before each session (GES-71) and compute packet metrics after each session (GES-70). The validator warns about trials that fall into packet gaps or disconnects (GES-72). Make one full-length capture with two receivers before the first participant session. | Muhammet Demir | Open |
| R3 | **No end-to-end recording yet.** The recording flow runs only with synthetic receivers. It is not yet connected to the host reader and the packet metrics, so no session has been recorded from real receivers (GES-75, C10). Model work in Sprint 5 needs real sessions. | High | High | Make this the first Sprint 5 story: connect the recording flow to the reader and the metrics. Record one internal test session and check it with the validator and the metrics tool before participant sessions. | Mustafa Batu Demir | Open |
| R4 | **Private data exposure.** This repository is public. A recording, a consent form, the code-to-name list or a name in a session file could be pushed by mistake, or a OneDrive folder could be shared too widely. | Low | High | Git ignores `data/*`. Real data lives only in the two team-only OneDrive folders, never shared as "anyone with the link". Session files use only the codes P1–P5 and R1, R2, …. The validator fails a session folder that holds other files or a code field that is not a code (GES-72). Reviewers check that a PR adds no data. | Muhammet Demir | Open (mitigated) |
| R5 | **Ethics decision.** Real participant data must not be collected before the ethics question is settled. | Low | High | Settled for the current scope. The advisor confirmed by e-mail on 21 Nov 2026 that no ethics application is needed, because this is an undergraduate project and its results will not be published ([Recording Procedure and Consent Form](https://gestura-se4910.atlassian.net/wiki/spaces/GP/pages/23101469/Recording+Procedure+and+Consent+Form), Section 8). Each participant gives written consent before the first session. Ask the advisor again before any publication. | Efecan Cengiz | Closed for the current scope |
| R6 | **Signal depends on placement, room and person.** The feasibility tests used one room and one board layout about 1 m apart. The results may not hold in other rooms, in other layouts or for other people (SoW, main risks). | High | High | Use the fixed placement procedure, with measured positions in `session.json` (GES-73). Evaluate with held-out day, participant and room (GES-74). Never use the final demonstration room for training. Report the limits; new-room calibration is later-stage work. | Mustafa Batu Demir | Open |
| R7 | **Collection schedule and open protocol questions.** The recording procedure plans about 50 cued sessions in 7 rooms, each room recorded on 3 days, plus one continuous recording per recording day (Section 4). The recording days, boards and people must be scheduled. The open questions in [`evaluation-splits-v1.md`](evaluation-splits-v1.md) must be decided before real collection; above all question 4, whether the recorder's code is added to `session.json`. | High | High | Plan the recording days in Sprint 5 planning. Decide open question 4 before the first session. Validate each session on the day it is recorded. | Sadıkhan Görmüş | Open |
| R8 | **Windows and macOS differences.** The team records on both systems, and Sprint 4 found three differences. On Windows, Python 3.12 gives `host_monotonic_ns` in 15 ms steps. On macOS, opening the port resets the receiver. Cue tones started late on macOS until PR #9 fixed it. A difference found late can make a session's labels or events wrong. | Medium | Medium | Use Python 3.13 or newer on Windows for cued sessions. The differences are documented in the reader and recording-flow READMEs. Run the tests on both systems during review. A CI story that runs the tests on Windows and macOS for every PR is proposed for Sprint 5 (GES-27). | Mert Aydul | Open |
| R9 | **Late dependent work.** The Sprint 4 stories depend on each other in a chain (GES-67 → GES-69 → GES-70 → GES-71 → GES-72), and the last ones were merged or opened in the final days of the sprint (GES-78). | Medium | Medium | In planning, start prerequisites first and record the dependencies in Jira. Start fixture-based work before the hardware is ready. Merge approved PRs without delay. | Sadıkhan Görmüş | Open |
| R10 | **Review load on one member.** The same member reviewed most Sprint 4 pull requests. This slows reviews and spreads knowledge of the code unevenly (GES-79). | Medium | Medium | Name a reviewer, different from the author, when a story is assigned. Rotate reviewers. Check the distribution at each sprint review. | Sadıkhan Görmüş | Open |

## 2. Technical-item mapping

The course or the advisor has not yet confirmed the format of technical items, for example a Technical Tree with
TIDs. This draft is therefore not presented as the course format. It links each Sprint 4 story to the architecture
component it builds and to the identifiers the Statement of Work already uses: deliverables (D), functionalities
(F), use cases (UC) and requirements (FR, NFR). When the format is confirmed, the same rows can be converted.

| Story | Summary | Owner | Component | SoW trace | Status (28 Nov) |
|---|---|---|---|---|---|
| GES-67 | Define the CSI stream, session schema and private storage layout | Muhammet Demir | Contract between all components; private dataset | D2, initial D6; F2.1; UC14, UC20; FR-03, FR-20, FR-21 | Done |
| GES-68 | Version and verify the tested ESP32 CSI sender and receiver firmware | Efecan Cengiz | Sender and receiver firmware | D1; F1.1; UC14; FR-01, FR-02 | Done |
| GES-69 | Parse sequenced CSI from one or two USB receivers | Mert Aydul | Host reader | D1; F1.1; UC14; FR-03, FR-04 | Done |
| GES-70 | Report receiver connection, packet rate and sequence-based loss | Muhammet Demir | Packet metrics | D1; F1.2; UC3, UC21; FR-05, FR-06; NFR-09 | Done |
| GES-71 | Build the scripted four-class CSI recording flow | Mustafa Batu Demir | Recording flow | D2; F2.1; UC20; FR-18, FR-19 | Done |
| GES-72 | Validate coded session metadata and the initial private dataset layout | Sadıkhan Görmüş | Session validator; private dataset | D2, initial D6; F2.1; UC20; FR-20, FR-21 | In Review |
| GES-73 | Document the recording protocol and consent handling | Efecan Cengiz | Private dataset (procedure and consent) | Initial D6; UC20; FR-20, FR-21 | Done |
| GES-74 | Define leakage-safe CSI evaluation splits and required metadata | Mustafa Batu Demir | Evaluation (design only) | D7 design; F5.1; UC22 design; NFR-01 to NFR-04 | Done |
| GES-75 | Write and execute an initial Sprint 4 acquisition test plan | Mert Aydul | All acquisition components | D10; F5.1; FR-01 to FR-06, FR-18 to FR-21 | Done |
| GES-76 | Draft the implementation risk list, architecture overview and technical-item mapping | Sadıkhan Görmüş | All components | D12 | In Progress |
| GES-77 to GES-79 | Retrospective actions | Sadıkhan Görmüş | Team process | None (process work) | GES-77 In Progress; GES-78, GES-79 To Do |

If the course asks for a tree, the same mapping can follow the five system parts of the SoW:

- **Part 1, Device software (D1)**
  - F1.1 Stream CSI with a fixed channel, fixed gain and sequence numbers: GES-68, GES-69
  - F1.2 Connection, packet rate and packet loss: GES-70
- **Part 2, Data collection, labeling and setup (D2, D3, D6)**
  - F2.1 Scripted sessions and session metadata: GES-67, GES-71, GES-72, GES-73
- **Part 3, Gesture recognition models (D4):** no Sprint 4 story
- **Part 4, Real-time service (D5):** no Sprint 4 story
- **Part 5, Evaluation infrastructure (D6, D7)**
  - F5.1 Versioned dataset and held-out splits: GES-74, GES-75
- **Cross-cutting:** D10 test plan and results: GES-75. D12 source code with setup instructions: GES-68, GES-76
