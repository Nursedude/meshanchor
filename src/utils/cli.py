"""CLI utilities and helpers"""

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.panel import Panel
import time

console = Console()


def create_progress():
    """Create a progress bar"""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    )


def show_success(message):
    """Show success message"""
    console.print(f"[bold green]✓[/bold green] {message}")


def show_error(message):
    """Show error message"""
    console.print(f"[bold red]✗[/bold red] {message}")


def show_warning(message):
    """Show warning message"""
    console.print(f"[bold yellow]⚠[/bold yellow] {message}")


def show_info(message):
    """Show info message"""
    console.print(f"[cyan]ℹ[/cyan] {message}")


def prompt_choice(message, choices, default=None):
    """Prompt user for a choice"""
    return Prompt.ask(message, choices=choices, default=default)


def prompt_confirm(message, default=True):
    """Prompt user for confirmation"""
    return Confirm.ask(message, default=default)


def show_table(title, headers, rows):
    """Display a table"""
    table = Table(title=title, show_header=True, header_style="bold magenta")

    for header in headers:
        table.add_column(header, style="cyan")

    for row in rows:
        table.add_row(*[str(item) for item in row])

    console.print(table)


def show_panel(content, title=None, style="cyan"):
    """Display a panel"""
    console.print(Panel(content, title=title, border_style=style))
