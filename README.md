# Gestura

Gestura recognizes hand gestures in real time from the Wi-Fi signal between ESP32-S3 boards, without a camera, and turns them into computer commands such as changing slides or playing and pausing media. It is the SE4910 graduation project of the GesturaYU team.

## How it works

- **Boards:** one ESP32-S3 sends Wi-Fi packets; two ESP32-S3 receivers measure the channel state information (CSI) of each packet and send it to a laptop over USB serial.
- **Gestures:** the core classes are push, slow wave, fast wave and no gesture. Composite gestures (double push, rhythms) are not separate classes; the real-time service recognizes them as sequences of core gestures. Hold, swipe and circle are stretch goals.
- **Pipeline:** the laptop reads CSI from both receivers, preprocesses it, classifies short windows with a trained model and sends the matching keyboard or media command.

## Status

Feasibility stage. The scripts that record and analyze CSI with one sender and one receiver are in [`src/gestura/acquisition/`](src/gestura/acquisition/README.md) and `src/gestura/evaluation/`; the [acquisition README](src/gestura/acquisition/README.md) explains how to set up the boards and run a recording. The other folders are placeholders for the parts below.

## Repository layout

```
firmware/
  sender/        sender board
  receiver/      receiver boards
  common/        packet format shared by the firmware and the Python code
src/gestura/
  acquisition/   reading CSI from the receivers (now: feasibility test scripts)
  dataset/       recordings, labels and sessions as training data
  processing/    preprocessing shared by training and the real-time service
  models/        model definitions and the threshold baseline
  training/      training and fine-tuning
  evaluation/    metrics and analysis (now: feasibility analysis scripts)
  runtime/       real-time service: receivers -> model -> commands
  app/
    collection/  data collection and labeling app
    setup/       setup app
notebooks/       Jupyter experiments
tests/           tests
data/            local recordings, never committed
docs/            documents
```

The system has five parts:

| Part | Folders | Technology |
| --- | --- | --- |
| Device software | `firmware/` | C, ESP-IDF, Espressif ESP-CSI |
| Data collection, labeling and setup | `acquisition/`, `dataset/`, `app/` | Python, pyserial, NumPy, pandas, PySide6 |
| Models | `models/`, `training/`, `notebooks/` | PyTorch, scikit-learn, Mantis (main), MOMENT (optional), Jupyter; fine-tuning on Colab or Kaggle GPUs |
| Real-time service | `runtime/` | asyncio, pyserial, PyTorch, pynput |
| Evaluation | `evaluation/`, `tests/` | pandas, scikit-learn, pytest |

### Rules between the folders

- `acquisition`, `dataset`, `processing` and `models` are the core. `training`, `evaluation`, `runtime` and `app` build on the core.
- `runtime` does not depend on `training`.
- Training and the real-time service use the same code in `processing`, so the live service gives the model the same kind of input it saw in training.
- `firmware/common` defines the packet format (sequence number, timestamp, receiver ID). It is the contract between the C firmware and the Python code.

## Data

Recordings and participant data are never committed. They go under `data/`, which is in `.gitignore`; only the empty folder is in the repository. The repository is public.

## Contributing

The git workflow, branch names, commit messages and review rules are in [CONTRIBUTING.md](CONTRIBUTING.md).
