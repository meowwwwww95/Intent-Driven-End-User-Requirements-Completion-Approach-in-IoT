def _encode_toon_value(v: object) -> str:
    s = str(v) if v is not None else ""
    if any(c in s for c in [",", ":", "{", "}", "[", "]", "\n"]):
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'
    return s

