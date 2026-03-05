from abc import ABC, abstractmethod
import pandas as pd
from typing import Optional, Dict, Any

class AbstractClient(ABC):
    @abstractmethod
    def sql(self, query: str, context: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        pass

    @abstractmethod
    def clone(self) -> "AbstractClient":
        pass

    @abstractmethod
    def close(self) -> None:
        pass