"""Two layers of models.

ExtractionState / ExtractedLane — what extraction (model or rules) produces
turn by turn. Facts only, no dollars, no pricing.

Proposal / ProposalLane — the final output, field-for-field matching
proposal.schema.json. pricing.py fills the `pricing` block; it is the only
place dollar figures get set. Keep these names and shapes in sync with the
schema — validate.py checks field names literally.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field

ServiceLevel = Literal["STANDARD", "EXPEDITED", "GUARANTEED"]
Mode = Literal["LTL", "FTL"]
UnserviceableReason = Literal[
    "lane_not_in_rate_card",
    "equipment_not_offered",
    "commodity_not_accepted",
    "exceeds_capacity",
]


class Customer(BaseModel):
    company: Optional[str] = None
    contact: Optional[str] = None
    industry: Optional[str] = None


# ---- Extraction stage: facts pulled from the transcript, no pricing yet ----

class ExtractedLane(BaseModel):
    origin_metro: str
    origin_state: str
    dest_metro: str
    dest_state: str
    pallets_per_shipment: Optional[float] = None
    weight_lb_per_pallet: Optional[float] = None
    shipments_per_month: Optional[float] = None
    service_level: ServiceLevel = "STANDARD"
    mode_requested: Optional[str] = None
    accessorials: list[str] = Field(default_factory=list)
    serviceable: bool = True
    unserviceable_reason: Optional[UnserviceableReason] = None
    notes: list[str] = Field(default_factory=list)


class ExtractionState(BaseModel):
    call_id: str
    customer: Customer = Field(default_factory=Customer)
    lanes: list[ExtractedLane] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


# ---- Final output: matches proposal.schema.json exactly ----

class AccessorialAmount(BaseModel):
    code: str
    amount: float


class LanePricing(BaseModel):
    linehaul: float
    fuel_surcharge: float
    accessorials: list[AccessorialAmount] = Field(default_factory=list)
    accessorials_total: float
    shipment_subtotal: float
    discount_pct: float
    discount: float
    shipment_total: float
    monthly_total: float


class ModeAlternative(BaseModel):
    mode: Mode
    shipment_total: float
    monthly_total: float
    transit_days: float


class ProposalLane(BaseModel):
    origin_metro: str
    origin_state: str
    dest_metro: str
    dest_state: str
    lane_id: Optional[str] = None
    serviceable: bool
    unserviceable_reason: Optional[UnserviceableReason] = None
    pallets_per_shipment: Optional[float] = None
    weight_lb_per_pallet: Optional[float] = None
    weight_lb_per_shipment: Optional[float] = None
    shipments_per_month: Optional[float] = None
    service_level: Optional[ServiceLevel] = None
    accessorials: list[str] = Field(default_factory=list)
    mode_quoted: Optional[Mode] = None
    mode_alternatives: list[ModeAlternative] = Field(default_factory=list)
    transit_days: Optional[float] = None
    rationale: Optional[str] = None
    pricing: Optional[LanePricing] = None


class VolumeTier(BaseModel):
    tier_name: str
    total_monthly_shipments: float
    discount_pct: float


class ExcludedItem(BaseModel):
    description: str
    reason: str


class ComparableAccount(BaseModel):
    account_name: str
    why: str


class Proposal(BaseModel):
    call_id: str
    customer: Customer
    deal_summary: str
    lanes: list[ProposalLane] = Field(min_length=1)
    volume_tier: VolumeTier
    monthly_total: float
    annual_total: float
    excluded: list[ExcludedItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    comparable_account: Optional[ComparableAccount] = None
