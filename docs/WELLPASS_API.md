# Wellpass API — Reverse Engineering Documentation
> Captured via mitmproxy on 12.06.2026 | App version: Wellpass 6.0 (Build 3058) | iOS 26.5

---

## Base URLs

| Service | Base URL |
|---|---|
| Netpulse / Auth | `https://qualitrain.netpulse.com` |
| Wellpass MWA API | `https://mwa-api.int.api.egym.com/mwa/api` |
| Gym Finder (legacy) | `https://gymfinder.int.api.egym.com` |

---

## Constants

```
BASEMENT_GYM_ID   = "158cc664-28d9-4e79-b8bd-883c9720cba7"
EXERCISER_ID      = "28dcac63-7111-4c93-b058-7b3ea26a68b3"   # user-specific
EGYM_LEGACY_ID    = "1032150"                                  # Basement legacy ID
BASEMENT_SLUG     = "muenchen-basement"
```

---

## Authentication

### Overview
3-step auth chain: Netpulse login → FLS token (Firebase JWT) → use as Bearer.
Token is valid ~60 minutes. JSESSIONID cookie valid 3 hours (allows multiple token refreshes without re-login).

### Required Headers (Netpulse endpoints)
```
x-np-user-agent:  clientType=MOBILE_DEVICE; devicePlatform=IOS; deviceUid=A79BC9C7-AC44-4706-9CA4-3CB715FE1676; applicationName=Wellpass; applicationVersion=6.0; applicationVersionCode=3058; containerName=QualitrainContainer;
x-np-app-version: 6.0
x-np-api-version: 1.5
Accept:           application/json,text/plain
User-Agent:       QualitrainContainer/6.0 (com.qualitrain.fitness; build:3058; iOS 26.5.0) Alamofire/5.9.1
```

### Required Headers (MWA API endpoints)
```
Authorization:    Bearer {accessToken}
Accept:           */*
Content-Type:     application/json
Accept-Language:  en-DE,en;q=0.5
User-Agent:       Wellpass/3058 CFNetwork/3860.600.12 Darwin/25.5.0
```

---

### Step 1 — Login

```
POST https://qualitrain.netpulse.com/np/exerciser/login
Content-Type: application/x-www-form-urlencoded
```

**Request Body (form-encoded):**
```
username={email}&password={password}
```
⚠️ Field is `username`, NOT `email`.

**Response (200 OK):**
```json
{
  "uuid": "28dcac63-7111-4c93-b058-7b3ea26a68b3",
  "firstName": "Ludwig",
  "lastName": "Matt",
  "verified": true,
  "emailVerified": true,
  "homeClubUuid": "938ee1f4-9209-447d-9070-8a474837edb8",
  "homeClubName": "Qualitrain",
  "chainUuid": "a67dbf27-af5a-11e8-885b-0ea8ba9e1fb0",
  "timezone": "Europe/Berlin",
  "membershipType": "Basic",
  "externalAuthToken": "F9lqKToyRVmFf6ZSBi9yH84cSGjTBWmhAdU0Roxu5tkvayDC1",
  "customInfo": {
    "egymAccountId": "aad31f43-3439-4242-95ca-ab76f37cdeb7"
  },
  "egymAccountId": "aad31f43-3439-4242-95ca-ab76f37cdeb7"
}
```

**Save from response:**
- `uuid` → exerciser ID (used in subsequent requests)
- `Set-Cookie: JSESSIONID=...` → session cookie (valid 3h)

---

### Step 2 — Get Bearer Token (FLS)

```
GET https://qualitrain.netpulse.com/np/micro-web-app/v1.0/exercisers/{exerciserId}/tokens/FLS
Cookie: JSESSIONID={sessionId}
```

**Response (200 OK):**
```json
{
  "provider": "FLS",
  "partner": "FLS",
  "accessToken": "eyJhbGci...",
  "accessTokenExpiresAt": "2026-06-12T12:59:29"
}
```

**Save from response:**
- `accessToken` → use as `Authorization: Bearer {token}` for all MWA API calls
- `accessTokenExpiresAt` → refresh token before this time

**Token Refresh Strategy:**
- JSESSIONID lasts 3 hours → call `/tokens/FLS` again without re-login
- After 3 hours → full re-login required
- Recommended: refresh token at `accessTokenExpiresAt - 5 minutes`

---

## Class Booking API

### Get Schedule

```
GET https://mwa-api.int.api.egym.com/mwa/api/class-booking/v1/class-session
    ?gymId={gymId}
    &status=ACTIVE
    &from={ISO8601}
    &to={ISO8601}
Authorization: Bearer {accessToken}
```

**Example:**
```
?gymId=158cc664-28d9-4e79-b8bd-883c9720cba7
&status=ACTIVE
&from=2026-06-12T00:00:00.000Z
&to=2026-06-19T23:59:59.999Z
```

**Response (200 OK) — Array of class sessions:**
```json
[
  {
    "id": "ecbb9323-5cbe-443a-927a-c82f6d116730",
    "gymMappingId": "a83e3fab-8da0-40ba-bf11-ad445a5f00f6",
    "name": "Strength",
    "description": "...",
    "startDateTime": "2026-06-14T07:45:00Z",
    "endDateTime":   "2026-06-14T08:45:00Z",
    "bookingWindowStart": "2026-05-09T07:45:00Z",
    "bookingWindowEnd":   "2026-06-14T07:40:00Z",
    "checkinStart": "2026-06-14T06:45:00Z",
    "checkinEnd":   "2026-06-14T09:15:00Z",
    "capacity": 24,
    "booked": 21,
    "waitlistCapacity": 24,
    "waitlistBooked": null,
    "instructor": {
      "fullName": "Andi"
    },
    "status": "ACTIVE",
    "classSessionType": "SCHEDULED_CLASS",
    "cancellationPolicy": {
      "cancellationWindow": "43200000",
      "lateCancelFee": "5.0",
      "noShowFee": "10.0"
    },
    "bookingLimit": {
      "value": 1,
      "timeframe": "DAY"
    },
    "gym": {
      "serverGymsId": "158cc664-28d9-4e79-b8bd-883c9720cba7",
      "name": "Basement - The Training Community München",
      "slug": "muenchen-basement"
    }
  }
]
```

**Availability Check Logic:**
```python
def is_available(session) -> bool:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    booking_end = datetime.fromisoformat(session["bookingWindowEnd"].replace("Z", "+00:00"))
    free_spots = session["capacity"] - session["booked"]
    return free_spots > 0 and booking_end > now
```

**Key fields:**
| Field | Description |
|---|---|
| `id` | Session UUID — use for booking |
| `name` | Class name (e.g. "Strength", "Mobility", "HYROX") |
| `startDateTime` | UTC datetime |
| `capacity` | Max participants |
| `booked` | Current bookings |
| `capacity - booked` | **Available spots** |
| `bookingWindowEnd` | Latest time to book (UTC) |
| `waitlistBooked` | null if no waitlist |

---

### Get Single Session

```
GET https://mwa-api.int.api.egym.com/mwa/api/class-booking/v1/class-session/{sessionId}
Authorization: Bearer {accessToken}
```

---

### Book a Class ⭐

```
POST https://mwa-api.int.api.egym.com/mwa/api/class-booking/v1/booking
Authorization: Bearer {accessToken}
Content-Type: application/json
```

**Request Body:**
```json
{"classSessionId": "ecbb9323-5cbe-443a-927a-c82f6d116730"}
```

**Response (200 OK):**
```json
{
  "id": "60a9a9e5-bf47-4289-a1eb-c108f6378ea4",
  "status": "BOOKED",
  "classSession": {
    "id": "ecbb9323-5cbe-443a-927a-c82f6d116730",
    "name": "Full Body Endurance (Engl.)",
    "startDateTime": "2026-06-18T05:30:00Z",
    "capacity": 24,
    "booked": 21
  },
  "cancellationPolicy": {
    "lateCancelFee": "5.0",
    "noShowFee": "10.0",
    "currencyCode": "EUR"
  },
  "hasFeeJoker": false
}
```

**Booking ID** (`id` in response) is needed for cancellation.

---

### Cancel a Booking

```
POST https://mwa-api.int.api.egym.com/mwa/api/class-booking/v1/booking/{bookingId}/cancel
Authorization: Bearer {accessToken}
```

---

### Get User's Bookings

```
GET https://mwa-api.int.api.egym.com/mwa/api/class-booking/v2/booking
    ?timeField=END_TIME
    &from={ISO8601}
    &status=BOOKED,CANCELLED_BY_GYM,WAITING_LIST,CHECKED_IN,NO_SHOW,CANCELLED_LATE,CANCELLED
    &order=ASC
Authorization: Bearer {accessToken}
```

---

## Gym Finder API

### Get Gym by Slug

```
GET https://mwa-api.int.api.egym.com/mwa/api/gym-finder/v1/gyms/{slug}
Authorization: Bearer {accessToken}
```

Example: `/gym-finder/v1/gyms/muenchen-basement`

**Response contains `serverGymsId`** — this is the `gymId` needed for class-session queries.

### Search Gyms

```
GET https://mwa-api.int.api.egym.com/mwa/api/gym-finder/v1/gyms/overview
    ?searchFilter=wellpass
    &limit=1000
    &latLong={lat}%3B{lng}
    &radius=30000
Authorization: Bearer {accessToken}
```

---

## User API

### Membership Info

```
GET https://mwa-api.int.api.egym.com/mwa/api/user/membership/access-info
Authorization: Bearer {accessToken}
```

---

## Notes

- All datetimes are **UTC** in ISO 8601 format
- The `bookingLimit` of `{"value": 1, "timeframe": "DAY"}` means **1 class per studio per day**
- Cancellation window is `43200000ms = 12 hours` — late cancel fee €5, no-show fee €10
- `waitlistCapacity: 24` means Wellpass has its own waitlist separate from bsport.io
- The app uses **Firebase JWT tokens** issued by Netpulse's FLS endpoint — no direct Firebase auth needed
- JSESSIONID cookie path: `/` — send on all Netpulse requests after login
- Bot should poll every **15–20 seconds** for optimal speed vs. rate limiting
