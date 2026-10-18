"""Delivery-address setup, shared by the reconcile graph and the chat agent.

``listAddresses`` must be the first call of any purchase flow, so both entry
points resolve it here (once) and reuse the result.
"""

from __future__ import annotations

from agent.mesa.gateway import Gateway, MCPError, raise_if_error
from agent.mesa.models import HostAddress, Session
from agent.mesa.parsing import decode, to_float


class NoAddress(RuntimeError):
    """The account has no saved address and the group has not given one."""


async def resolve_address(gateway: Gateway, session: Session) -> HostAddress:
    """Pick the delivery address: last used, else most used, else the first one."""
    if session.host_address:
        return session.host_address

    data = raise_if_error("listAddresses", decode(await gateway.call("listAddresses")))
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise MCPError("listAddresses", "O iFood retornou uma resposta de enderecos incompleta; nao foi possivel confirmar o endereco.")
    rows = data["results"]
    if not rows:
        raise NoAddress(
            "O iFood retornou uma lista vazia de enderecos para a conta autenticada. "
            "Confira se o login e da conta correta e se o endereco esta salvo no app do iFood."
        )
    rows = [row for row in rows if isinstance(row, dict) and row.get("addressId")
            and isinstance(row.get("coordinates"), dict)
            and all(row["coordinates"].get(key) is not None for key in ("latitude", "longitude"))]
    if not rows:
        raise MCPError("listAddresses", "Os enderecos retornados pelo iFood estao sem identificador ou coordenadas validas.")
    row = next((r for r in rows if r.get("isLastUsedAddress")), None) or next(
        (r for r in rows if r.get("isMostUsedAddress30d")), rows[0]
    )
    coords = row["coordinates"]
    session.host_address = HostAddress(
        address_id=row["addressId"],
        alias=row.get("alias") or "Endereco",
        label=row.get("formattedAddress", ""),
        latitude=to_float(coords["latitude"]),
        longitude=to_float(coords["longitude"]),
    )
    return session.host_address


def location_of(session: Session) -> dict[str, float]:
    assert session.host_address, "resolve_address() must run first"
    return {
        "latitude": session.host_address.latitude,
        "longitude": session.host_address.longitude,
    }
