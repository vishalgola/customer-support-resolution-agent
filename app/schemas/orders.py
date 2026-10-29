from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_name: str
    quantity: int
    paid_amount_minor: int


class ReturnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    received_at: datetime | None
    inspection_status: str


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    currency: str
    paid_amount_minor: int
    placed_at: datetime
    delivered_at: datetime | None
    items: list[ItemOut]
    returns: list[ReturnOut]