"""Terminal presentation and prompts, with a plain fallback for basic terminals."""

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


class CancelAction(Exception):
    """The user returned from a nested menu."""


@dataclass
class OperationResult:
    device: Any
    output: str = ""
    error: str = ""
    elapsed: float = 0


class UI:
    def __init__(self, *, plain=False, no_color=False):
        self.plain = plain
        no_color = no_color or "NO_COLOR" in os.environ
        self.console = Console(
            no_color=no_color,
            highlight=False,
            theme=Theme(
                {
                    "accent": "cyan",
                    "success": "green",
                    "warning": "yellow",
                    "error": "red",
                    "muted": "dim",
                }
            ),
        )
        self.error_console = Console(stderr=True, no_color=no_color, highlight=False)
        self.arrows = not plain and sys.stdin.isatty() and self.console.is_terminal
        self.style = questionary.Style(
            [
                (name, "noinherit")
                for name in (
                    "qmark",
                    "question",
                    "answer",
                    "pointer",
                    "selected",
                    "instruction",
                    "text",
                    "search_success",
                    "search_none",
                )
            ]
            if no_color
            else [
                ("qmark", "fg:#5fd7d7 bold"),
                ("question", "bold"),
                ("pointer", "fg:#5fd7d7 bold"),
                ("highlighted", "fg:#5fd7d7 bold"),
                ("selected", "fg:#5fd7d7"),
                ("answer", "fg:#5fd7d7"),
            ]
        )

    def banner(self):
        self.console.print(
            Panel(
                Text.assemble(
                    ("ALC", "bold cyan"),
                    "  /  Android 设备控制台\n",
                    ("设备 · 应用 · 配置 · 批量操作", "dim"),
                ),
                border_style="cyan",
                padding=(1, 2),
                box=box.ROUNDED,
            )
        )

    def info(self, message, *, style=None):
        self.console.print(Text(str(message), style=style))

    def error(self, message):
        self.error_console.print(Text(f"错误：{message}", style="red"))

    def choose(self, title, options):
        """Options are (stable key, visible label) pairs."""
        if self.arrows:
            answer = questionary.select(
                title,
                choices=[questionary.Choice(label, value=key) for key, label in options],
                style=self.style,
                instruction="（↑↓ 选择 · Enter 确认 · Ctrl+C 返回）",
            ).unsafe_ask()
            if answer is None:
                raise CancelAction
            return answer
        self.console.print(Panel(Text(title, style="bold"), border_style="cyan"))
        for key, label in options:
            self.info(f"  {key:>2}  {label}")
        keys = {key for key, _ in options}
        while True:
            answer = Prompt.ask("选项", console=self.console).strip().lower()
            if answer in keys:
                return answer
            self.error("请输入菜单中的选项")

    def text(self, message, *, default=None, validator=None):
        while True:
            value = Prompt.ask(
                Text(message),
                console=self.console,
                default=default,
            ).strip()
            if value.lower() in {"q", "back", "返回"}:
                raise CancelAction
            if not value:
                self.error("内容不能为空；输入 q 返回")
                continue
            try:
                return validator(value) if validator else value
            except (ValueError, OSError, argparse.ArgumentTypeError) as exc:
                self.error(exc)

    def confirm(self, message, *, default=False):
        return Confirm.ask(Text(message), console=self.console, default=default)

    def device_table(self, devices, states=None, selected=()):
        table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
        table.add_column("ID", justify="right", style="accent", no_wrap=True)
        table.add_column("设备", style="bold")
        table.add_column("ADB 地址", overflow="fold")
        table.add_column("状态", no_wrap=True)
        table.add_column("选择", no_wrap=True)
        state_labels = {
            "device": ("在线", "success"),
            "offline": ("离线", "error"),
            "unauthorized": ("待授权", "warning"),
            "disconnected": ("未连接", "muted"),
            "unknown": ("未知", "warning"),
        }
        for device in devices:
            state = (states or {}).get(device.serial, "disconnected")
            label, style = state_labels.get(state, (state, "muted"))
            table.add_row(
                str(device.id),
                Text(device.name),
                Text(device.serial),
                Text(label, style=style),
                "●" if device.id in selected else "",
            )
        self.console.print(table)

    def pick_devices(self, devices, selected=(), *, single=False):
        """Return checkbox IDs or None to request textual range input."""
        choices = [
            questionary.Choice(
                f"{d.id:>2}  {d.name}  ·  {d.serial}", value=d.id, checked=d.id in selected
            )
            for d in devices
        ]
        if single:
            choices.append(questionary.Choice("返回", value="__back__"))
            result = questionary.select(
                "选择一台设备",
                choices=choices,
                style=self.style,
                instruction="（↑↓ 选择 · Enter 确认 · Ctrl+C 返回）",
            ).unsafe_ask()
            if result is None or result == "__back__":
                raise CancelAction
            return [result]
        mode = self.choose(
            "选择操作范围",
            [
                ("pick", "选择设备（空格勾选，Enter 确认）"),
                ("range", "输入 ID / 范围，如 1,3,5-8"),
                ("all", f"全部设备（{len(devices)} 台）"),
                ("back", "返回"),
            ],
        )
        if mode == "back":
            raise CancelAction
        if mode == "range":
            return None
        if mode == "all":
            return [device.id for device in devices]
        answer = questionary.checkbox(
            "选择设备",
            choices=choices,
            style=self.style,
            instruction="（空格 勾选 · a 全选 · Enter 确认 · Ctrl+C 返回）",
            validate=lambda values: True if values else "请至少选择一台设备",
        ).unsafe_ask()
        if answer is None:
            raise CancelAction
        return answer

    def target_summary(self, title, devices):
        body = Text.assemble(
            (f"{title}\n", "bold"),
            f"目标 {len(devices)} 台：",
            ", ".join(f"{d.id} · {d.name}" for d in devices),
        )
        self.console.print(Panel(body, border_style="cyan", box=box.ROUNDED))

    def progress(self):
        return Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.completed}/{task.total} 台"),
            TimeElapsedColumn(),
            console=self.console,
            disable=self.plain or not self.console.is_terminal,
        )

    def results(self, title, results):
        table = Table(title=Text(title), box=box.SIMPLE, expand=True, show_lines=True)
        table.add_column("ID", justify="right", style="accent", no_wrap=True)
        table.add_column("设备")
        table.add_column("结果", no_wrap=True)
        table.add_column("耗时", justify="right", no_wrap=True)
        table.add_column("详情", ratio=3, overflow="fold")
        for result in results:
            failed = bool(result.error)
            table.add_row(
                str(result.device.id),
                Text(result.device.name),
                Text("失败" if failed else "完成", style="error" if failed else "success"),
                f"{result.elapsed:.1f}s",
                Text(result.error or result.output or "完成"),
            )
        self.console.print(table)
        failed = sum(bool(result.error) for result in results)
        self.info(
            f"共 {len(results)} 台 · 成功 {len(results) - failed} · 失败 {failed}",
            style="warning" if failed else "success",
        )
