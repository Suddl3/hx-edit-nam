"""Reject the reproduced x86 entry/interior hook conflict before target startup."""
import re


def check_model_hook_pair(source):
    # Deliberately scoped to reviewed constant attachments, not a general JS or
    # function-range analyzer. This catches the shipped10c wiring and direct
    # address equivalents without claiming all other hooks safe.
    aliases = {name: int(value, 16) for name, value in re.findall(
        r'const\s+(\w+)\s*=\s*address\(\s*(0x[0-9a-fA-F]+)\s*\)', source)}
    sites = set()
    for direct, alias in re.findall(
            r'Interceptor\.attach\(\s*(?:address\(\s*(0x[0-9a-fA-F]+)\s*\)|(\w+))\s*,', source):
        value = int(direct, 16) if direct else aliases.get(alias)
        if value is not None:
            sites.add(value)
    if {0x26b9b0, 0x26bfed} <= sites:
        raise ValueError('Unsafe Model entry/interior hook pair: this reproduced an x86 startup crash. Use the corrected candidate.')
    return sites
