"""TEMPORARY stand-in for tools/sft/serialize.py (same interface), for arena smoke tests only.

Used only when HARDREAD_SERIALIZER points here. Never use it for a gate evaluation: the
model must see the real serializer's prompt, identical to its SFT data.
"""
import json


def build_example(view: dict, history: list, legal: list, player: str, turn: int, k: int,
                  team_preview: bool, task: str = "action") -> dict:
    id_map = {f"a{i}": a for i, a in enumerate(legal)}
    menu = "\n".join(f"{cid}: {a}" for cid, a in id_map.items())
    user = (f"You are {player}. Turn {turn}, decision {k}{' (team preview)' if team_preview else ''}.\n"
            f"State: {json.dumps(view, separators=(',', ':'))}\n"
            f"Recent events:\n" + "\n".join(history[-40:]) + f"\nLegal actions:\n{menu}")
    tool = {"type": "function", "function": {
        "name": "choose_action", "description": "Choose one legal action by id.",
        "parameters": {"type": "object", "properties": {"choice_id": {"type": "string", "enum": list(id_map)}},
                       "required": ["choice_id"]}}}
    return {"messages": [{"role": "system", "content": "Pick the best Pokemon Showdown action."},
                         {"role": "user", "content": user}],
            "tools": [tool], "tool_choice": "required", "id_map": id_map}
