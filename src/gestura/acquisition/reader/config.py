"""Host configuration: board label -> USB serial number, and the expected link settings.

Example (receivers.example.json in this folder)::

    {
      "boards": {"B2": "5C37270507", "B3": "<serial of the second receiver>"},
      "link": {"channel": 11, "bandwidth": 0, "secondary_channel": 0, "stbc": 0}
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .reader import LinkConfig, ReceiverSpec


def load_host_config(path) -> Tuple[Dict[str, str], LinkConfig]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    boards = data.get("boards")
    if not isinstance(boards, dict) or not boards:
        raise ValueError(f"{path}: 'boards' must map board labels to USB serial numbers")
    link = LinkConfig(**data.get("link", {}))
    return {str(k): str(v) for k, v in boards.items()}, link


def receiver_specs(assignments: Sequence[str], boards: Dict[str, str]) -> List[ReceiverSpec]:
    """Turn ["rx1=B2", "rx2=B3"] into receiver specs using the board table."""
    specs = []
    for item in assignments:
        receiver_id, sep, board_id = item.partition("=")
        if not sep or not receiver_id or not board_id:
            raise ValueError(f"expected <receiver_id>=<board label>, got {item!r}")
        if board_id not in boards:
            raise ValueError(f"board {board_id!r} is not in the host configuration")
        specs.append(ReceiverSpec(receiver_id, board_id, boards[board_id]))
    return specs
