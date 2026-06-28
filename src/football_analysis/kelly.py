"""Kelly criterion and bankroll management for position sizing."""

from pydantic import BaseModel, Field


class BankrollSettings(BaseModel):
    """Bankroll configuration for Kelly criterion."""

    initial_units: float = Field(default=10000.0, gt=0.0)
    unit_basis: str = "fixed"


class KellySettings(BaseModel):
    """Kelly criterion position sizing configuration."""

    mode: str = "fractional"
    fraction: float = Field(default=0.25, ge=0.0, le=1.0)
    min_edge: float = Field(default=0.025, ge=0.0)
    max_stake_fraction: float = Field(default=0.005, ge=0.0, le=1.0)


def calculate_kelly_stake(
    edge: float,
    odds: float,
    bankroll_units: float,
    settings: KellySettings,
    *,
    stake_cap_units: float | None = None,
) -> float:
    """Calculate position size using fractional Kelly plus hard caps."""
    if edge < settings.min_edge:
        return 0.0

    if odds <= 1.0 or bankroll_units <= 0:
        return 0.0

    kelly_fraction = edge / (odds - 1.0)
    if kelly_fraction <= 0:
        return 0.0

    if settings.mode == "fractional":
        kelly_fraction = kelly_fraction * settings.fraction

    stake = bankroll_units * kelly_fraction
    caps = [bankroll_units * settings.max_stake_fraction]
    if stake_cap_units is not None:
        caps.append(max(0.0, stake_cap_units))
    stake = min(stake, *caps)
    return round(stake, 3)


def calculate_kelly_fraction(
    edge: float,
    odds: float,
    settings: KellySettings,
) -> float:
    """Return the configured Kelly fraction after applying safety factor."""
    if edge < settings.min_edge or odds <= 1.0:
        return 0.0
    full_fraction = kelly_fraction_for_display(edge, odds)
    if settings.mode == "fractional":
        full_fraction *= settings.fraction
    return round(max(0.0, full_fraction), 6)


def kelly_fraction_for_display(
    edge: float,
    odds: float,
) -> float:
    """Return pure Kelly fraction (before applying safety factor)."""
    if edge <= 0 or odds <= 1.0:
        return 0.0
    return edge / (odds - 1.0)


def validate_edge(edge: float, min_edge: float) -> bool:
    """Check if edge meets minimum threshold."""
    return edge >= min_edge
