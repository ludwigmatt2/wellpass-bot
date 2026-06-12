import httpx
import logging
from datetime import datetime, timezone, timedelta
from wellpass.models import TokenResponse

logger = logging.getLogger(__name__)

NETPULSE_BASE = "https://qualitrain.netpulse.com"

_NP_HEADERS = {
    "x-np-user-agent": (
        "clientType=MOBILE_DEVICE; devicePlatform=IOS; "
        "deviceUid=A79BC9C7-AC44-4706-9CA4-3CB715FE1676; "
        "applicationName=Wellpass; applicationVersion=6.0; "
        "applicationVersionCode=3058; containerName=QualitrainContainer;"
    ),
    "x-np-app-version": "6.0",
    "x-np-api-version": "1.5",
    "Accept": "application/json,text/plain",
    "User-Agent": "QualitrainContainer/6.0 (com.qualitrain.fitness; build:3058; iOS 26.5.0) Alamofire/5.9.1",
}


async def login(email: str, password: str) -> tuple[str, str, str]:
    """Returns (exerciser_id, jsessionid, display_name). Raises on failure."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{NETPULSE_BASE}/np/exerciser/login",
            data={"username": email, "password": password},
            headers={**_NP_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        jsessionid = resp.cookies.get("JSESSIONID")
        if not jsessionid:
            raise ValueError("No JSESSIONID in login response")
        display_name = f"{data.get('firstName', '')} {data.get('lastName', '')}".strip()
        return data["uuid"], jsessionid, display_name


async def fetch_fls_token(exerciser_id: str, jsessionid: str) -> TokenResponse:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{NETPULSE_BASE}/np/micro-web-app/v1.0/exercisers/{exerciser_id}/tokens/FLS",
            headers={**_NP_HEADERS, "Cookie": f"JSESSIONID={jsessionid}"},
        )
        resp.raise_for_status()
        return TokenResponse(**resp.json())


async def get_valid_token(user: dict, db) -> str:
    """Return a valid Bearer token, refreshing as needed. Mutates DB as required."""
    now = datetime.now(timezone.utc)

    token_expires = user.get("token_expires")
    if token_expires:
        if isinstance(token_expires, str):
            token_expires = datetime.fromisoformat(token_expires.replace("Z", "+00:00"))
        if token_expires.tzinfo is None:
            token_expires = token_expires.replace(tzinfo=timezone.utc)
        if token_expires - timedelta(minutes=5) > now:
            return user["access_token"]

    session_expires = user.get("session_expires")
    if session_expires:
        if isinstance(session_expires, str):
            session_expires = datetime.fromisoformat(session_expires.replace("Z", "+00:00"))
        if session_expires.tzinfo is None:
            session_expires = session_expires.replace(tzinfo=timezone.utc)
        if session_expires > now:
            token_data = await fetch_fls_token(user["exerciser_id"], user["jsessionid"])
            await db.update_token(
                user["id"],
                token_data.accessToken,
                token_data.expires_at_utc().isoformat(),
            )
            return token_data.accessToken

    from core.crypto import decrypt
    password = decrypt(user["wellpass_pass"])
    exerciser_id, jsessionid, _ = await login(user["wellpass_email"], password)
    token_data = await fetch_fls_token(exerciser_id, jsessionid)
    session_exp = (now + timedelta(hours=3)).isoformat()
    await db.update_user_session(
        user["id"],
        exerciser_id,
        jsessionid,
        session_exp,
        token_data.accessToken,
        token_data.expires_at_utc().isoformat(),
    )
    return token_data.accessToken
