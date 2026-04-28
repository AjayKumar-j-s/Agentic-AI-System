from __future__ import annotations

from typing import Dict


def get_order_status(order_id: str) -> Dict[str, str]:
    # Mock tool implementation; replace with real OMS integration.
    # This tool intentionally returns minimal data (data minimization).
    return {"order_id": order_id, "status": "shipped"}


def get_shipment_tracking(order_id: str) -> Dict[str, str]:
    # Mock tool implementation; replace with carrier/shipping integration.
    return {"order_id": order_id, "carrier": "MockCarrier", "tracking_number": f"TRK-{order_id[-6:]}"}

