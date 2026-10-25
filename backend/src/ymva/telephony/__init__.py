from typing import Any

from ymva.models import TelephonyProvider
from ymva.telephony.base import TelephonyError, TelephonyProviderClient
from ymva.telephony.plivo_family import PlivoClient, VobizClient

PROVIDERS: dict[TelephonyProvider, type[TelephonyProviderClient]] = {
    TelephonyProvider.PLIVO: PlivoClient,
    TelephonyProvider.VOBIZ: VobizClient,
}

SUPPORTED = tuple(PROVIDERS)


def client_for(provider: TelephonyProvider, credentials: dict[str, Any]) -> TelephonyProviderClient:
    cls = PROVIDERS.get(provider)
    if cls is None:
        raise TelephonyError(f"{provider.value} isn't supported yet")
    return cls(credentials)


__all__ = ["PROVIDERS", "SUPPORTED", "TelephonyError", "TelephonyProviderClient", "client_for"]
