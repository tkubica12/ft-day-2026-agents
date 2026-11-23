from __future__ import annotations

import errno
import logging
import time
from typing import Any

from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.authentication.msal.msal_auth import MsalAuth
from microsoft_agents.hosting.core import AuthTypes
from msal import ConfidentialClientApplication, ManagedIdentityClient, UserAssignedManagedIdentity
from requests import Session
from requests.exceptions import ConnectionError


logger = logging.getLogger(__name__)
RETRY_DELAYS = (0.2, 0.4)
IDENTITY_TIMEOUT = (2, 3)


def is_connection_reset(error: ConnectionError) -> bool:
    pending: list[Any] = [error]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, ConnectionResetError) and value.errno in {104, errno.ECONNRESET}:
            return True
        if isinstance(value, BaseException):
            pending.extend(value.args)
            pending.extend((value.__cause__, value.__context__))
        elif isinstance(value, (tuple, list)):
            pending.extend(value)
    return False


class AssertionIdentitySession(Session):
    def get(self, url: str, **kwargs: Any) -> Any:
        kwargs["timeout"] = IDENTITY_TIMEOUT
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                return super().get(url, **kwargs)
            except ConnectionError as error:
                if attempt == len(RETRY_DELAYS) or not is_connection_reset(error):
                    raise
                logger.warning("Managed identity assertion connection reset; retrying (%d/2)", attempt + 1)
                time.sleep(RETRY_DELAYS[attempt])
        raise RuntimeError("Managed identity assertion retry loop ended unexpectedly")


class AssertionRetryMsalAuth(MsalAuth):
    def _create_client_application(self, tenant_id: str | None = None) -> Any:
        config = self._msal_configuration
        if config.AUTH_TYPE != AuthTypes.federated_credentials:
            return super()._create_client_application(tenant_id)

        identity = ManagedIdentityClient(
            UserAssignedManagedIdentity(client_id=config.FEDERATED_CLIENT_ID),
            http_client=AssertionIdentitySession(),
        )

        def assertion() -> str:
            result = identity.acquire_token_for_client(resource="api://AzureADTokenExchange")
            if "access_token" not in result:
                raise ValueError("Failed to acquire managed identity assertion")
            return result["access_token"]

        return ConfidentialClientApplication(
            client_id=config.CLIENT_ID,
            authority=MsalAuth._resolve_authority(config, tenant_id),
            client_credential={"client_assertion": assertion},
            azure_region=MsalAuth._resolve_azure_region(config),
        )


class AssertionRetryConnectionManager(MsalConnectionManager):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        for name, config in self._config_map.items():
            if config.AUTH_TYPE == AuthTypes.federated_credentials:
                self._connections[name] = AssertionRetryMsalAuth(config)
