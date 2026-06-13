import logging
from pydantic import BaseModel, field_validator
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class TokenResponse(BaseModel):
    accessToken: str
    accessTokenExpiresAt: str

    def expires_at_utc(self) -> datetime:
        try:
            dt = datetime.fromisoformat(self.accessTokenExpiresAt.replace("Z", "+00:00"))
        except (ValueError, TypeError) as e:
            # Degrade to a conservative short expiry so a parse glitch triggers
            # more-frequent refresh instead of a hard crash.
            logger.warning(
                f"Could not parse accessTokenExpiresAt={self.accessTokenExpiresAt!r}: {e} "
                "— falling back to ~50min expiry"
            )
            return datetime.now(timezone.utc) + timedelta(minutes=50)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


class Instructor(BaseModel):
    fullName: str


class CancellationPolicy(BaseModel):
    cancellationWindow: Optional[str] = None
    lateCancelFee: Optional[str] = None
    noShowFee: Optional[str] = None


class BookingLimit(BaseModel):
    value: int
    timeframe: str


class GymInfo(BaseModel):
    serverGymsId: str
    name: str
    slug: str


class ClassSession(BaseModel):
    id: str
    name: str
    startDateTime: str
    endDateTime: str
    bookingWindowEnd: str
    capacity: int
    booked: int
    waitlistBooked: Optional[int] = None
    instructor: Optional[Instructor] = None
    status: str
    gym: Optional[GymInfo] = None
    cancellationPolicy: Optional[CancellationPolicy] = None

    def start_dt(self) -> datetime:
        return datetime.fromisoformat(self.startDateTime.replace("Z", "+00:00"))

    def booking_end_dt(self) -> datetime:
        return datetime.fromisoformat(self.bookingWindowEnd.replace("Z", "+00:00"))

    def free_spots(self) -> int:
        return self.capacity - self.booked

    def is_available(self) -> bool:
        now = datetime.now(timezone.utc)
        return self.free_spots() > 0 and self.booking_end_dt() > now

    def availability_emoji(self) -> str:
        spots = self.free_spots()
        if spots == 0:
            return "🔴"
        if spots <= 3:
            return "🟡"
        return "🟢"


class BookingResponse(BaseModel):
    id: str
    status: str
    classSession: dict
    cancellationPolicy: Optional[dict] = None
    hasFeeJoker: Optional[bool] = None


class GymOverview(BaseModel):
    serverGymsId: str
    name: str
    slug: str
    city: Optional[str] = None
    street: Optional[str] = None
