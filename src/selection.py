"""Validate the same device selection syntax for menus and commands."""

import re
from collections.abc import Collection


def parse_device_ids(spec: str, available: Collection[int]) -> list[int]:
    """Accept all / 0, comma or space separated IDs and inclusive ranges."""
    spec = spec.strip().lower()
    known = set(available)
    if not spec:
        raise ValueError("请选择至少一台设备")
    if spec in {"0", "all", "全部"}:
        if not known:
            raise ValueError("设备列表为空，请先填写设备配置")
        return sorted(known)

    selected = []
    for part in re.split(r"[,，\s]+", spec):
        match = re.fullmatch(r"([1-9]\d*)(?:-([1-9]\d*))?", part)
        if not match:
            raise ValueError(f"无效的设备选择 {part!r}，示例：1,3,5-8；0 表示全部")
        start = int(match[1])
        end = int(match[2] or start)
        if start > end:
            raise ValueError(f"范围 {part!r} 的起始 ID 不能大于结束 ID")
        # Validate against known IDs before expanding an unbounded input range.
        members = sorted(value for value in known if start <= value <= end)
        if len(members) != end - start + 1:
            raise ValueError(f"设备 {part!r} 不在配置中，请查看设备列表")
        selected.extend(members)
    return list(dict.fromkeys(selected))
