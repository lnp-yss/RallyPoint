"""Validate Bluetooth web requests without invoking hardware."""
import re


def validate_action(action: str, address: str) -> tuple[str, str]:
    if action not in {'enable', 'scan', 'pair', 'connect', 'disconnect', 'forget'}:
        raise ValueError('Unsupported Bluetooth action')
    if not isinstance(address, str) or (action not in {'enable', 'scan'} and not
            re.fullmatch(r'[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}', address)):
        raise ValueError('Select a valid Bluetooth device')
    return action, address.upper()