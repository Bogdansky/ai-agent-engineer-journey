from .base import Scenario
from .s01_pool_leak import SCENARIO as S01
from .s02_config_typo import SCENARIO as S02
from .s03_no_timeout import SCENARIO as S03

REGISTRY: dict[str, Scenario] = {s.id: s for s in (S01, S02, S03)}

__all__ = ["REGISTRY", "Scenario"]
