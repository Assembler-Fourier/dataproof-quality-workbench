"""Public contract and result schemas shared by HTTP and CLI clients."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ColumnType = Literal["string", "integer", "number", "date", "boolean"]


class ColumnRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: ColumnType = "string"
    required: bool = True
    nullable: bool = True
    unique: bool = False
    allowed_values: list[str] | None = Field(default=None, max_length=200)
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    @model_validator(mode="after")
    def valid_bounds(self) -> "ColumnRule":
        if (self.minimum is not None or self.maximum is not None) and self.type not in {"integer", "number"}:
            raise ValueError("Numeric bounds require an integer or number column")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Untitled contract", min_length=1, max_length=120)
    version: Literal[1] = 1
    allow_extra_columns: bool = True
    columns: dict[str, ColumnRule] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def valid_names(self) -> "Contract":
        if any(not name.strip() or name != name.strip() or len(name) > 200 for name in self.columns):
            raise ValueError("Column names must be nonempty, trimmed, and at most 200 characters")
        return self


class Violation(BaseModel):
    row: int | None
    column: str
    rule: str
    value: str | None
    value_truncated: bool = False
    message: str


class Report(BaseModel):
    contract_name: str
    filename: str
    passed: bool
    total_rows: int
    valid_rows: int
    rejected_rows: int
    total_violations: int
    schema_violations: int
    violations: list[Violation]
    violations_truncated: bool
    rule_counts: dict[str, int]
    rejected_row_numbers: list[int]
    quality_percent: float
