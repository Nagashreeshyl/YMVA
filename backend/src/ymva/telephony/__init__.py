from typing import Any

from ymva.models import TelephonyProvider
from ymva.telephony.base import TelephonyError, TelephonyProviderClient
from ymva.telephony.exotel import ExotelClient
from ymva.telephony.plivo_family import PlivoClient, VobizClient
from ymva.telephony.simulator import SimulatorClient
from ymva.telephony.twilio_family import TelnyxClient, TwilioClient
from ymva.telephony.vonage import VonageClient

PROVIDERS: dict[TelephonyProvider, type[TelephonyProviderClient]] = {
    TelephonyProvider.PLIVO: PlivoClient,
    TelephonyProvider.VOBIZ: VobizClient,
    TelephonyProvider.EXOTEL: ExotelClient,
    TelephonyProvider.TWILIO: TwilioClient,
    TelephonyProvider.TELNYX: TelnyxClient,
    TelephonyProvider.VONAGE: VonageClient,
    TelephonyProvider.SIMULATOR: SimulatorClient,
}

# Shown on the Integrations page; the simulator is created by `ymva simulate` only.
SUPPORTED = tuple(p for p in PROVIDERS if p != TelephonyProvider.SIMULATOR)


def client_for(provider: TelephonyProvider, credentials: dict[str, Any]) -> TelephonyProviderClient:
    cls = PROVIDERS.get(provider)
    if cls is None:
        raise TelephonyError(f"{provider.value} isn't supported yet")
    return cls(credentials)


__all__ = ["PROVIDERS", "SUPPORTED", "TelephonyError", "TelephonyProviderClient", "client_for"]
