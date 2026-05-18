from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Subgoal:
    id: str                          # "A", "B", "C", etc.
    title: str
    status: Literal["inline", "split"]
    content: str = ""                # present when status == "inline"
    child_plan: str = ""             # plan name when status == "split"


@dataclass
class Plan:
    name: str                        # slug, matches filename stem
    title: str                       # H1 heading
    parent: str = ""                 # parent plan name, empty for root
    status: str = "active"           # active | complete | archived
    sections: dict[str, str] = field(default_factory=dict)
    subgoals: list[Subgoal] = field(default_factory=list)

    def get_section(self, name: str) -> str:
        name_lower = name.lower()
        return next((v for k, v in self.sections.items() if k.lower() == name_lower), "")

    def section_names(self) -> list[str]:
        return list(self.sections.keys())
