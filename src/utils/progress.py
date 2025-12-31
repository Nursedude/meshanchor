"""Progress indicator utilities for long-running operations"""

import subprocess
import sys
import time
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn
)

console = Console()


def run_with_progress(command, description, shell=False, timeout=600):
    """Run a command with a progress spinner

    Args:
        command: Command to run (string or list)
        description: Description to show during progress
        shell: Use shell execution
        timeout: Maximum time to wait (seconds)

    Returns:
        dict: {success: bool, stdout: str, stderr: str, returncode: int}
    """
    if isinstance(command, str) and not shell:
        command = command.split()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task(description, total=None)

        try:
            process = subprocess.Popen(
                command,
                shell=shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            stdout, stderr = process.communicate(timeout=timeout)

            return {
                'success': process.returncode == 0,
                'stdout': stdout,
                'stderr': stderr,
                'returncode': process.returncode
            }
        except subprocess.TimeoutExpired:
            process.kill()
            return {
                'success': False,
                'stdout': '',
                'stderr': 'Command timed out',
                'returncode': -1
            }
        except Exception as e:
            return {
                'success': False,
                'stdout': '',
                'stderr': str(e),
                'returncode': -1
            }


def run_with_live_progress(command, description, shell=False, timeout=600):
    """Run a command with live progress bar and streaming output

    Parses apt-get style progress and shows a progress bar.

    Args:
        command: Command to run (string or list)
        description: Description to show during progress
        shell: Use shell execution
        timeout: Maximum time to wait (seconds)

    Returns:
        dict: {success: bool, stdout: str, stderr: str, returncode: int}
    """
    if isinstance(command, str) and not shell:
        command = command.split()

    stdout_lines = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task(description, total=100)

        try:
            process = subprocess.Popen(
                command,
                shell=shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            # Parse output and update progress
            for line in process.stdout:
                stdout_lines.append(line)

                # Try to parse apt-get progress (e.g., "Progress: [50%]")
                if 'Progress:' in line or '%' in line:
                    try:
                        # Look for percentage in line
                        import re
                        match = re.search(r'(\d+)%', line)
                        if match:
                            pct = int(match.group(1))
                            progress.update(task, completed=pct)
                    except (ValueError, IndexError):
                        pass

                # Update description with current action
                if 'Setting up' in line:
                    progress.update(task, description=f"[cyan]Setting up packages...[/cyan]")
                elif 'Unpacking' in line:
                    progress.update(task, description=f"[cyan]Unpacking packages...[/cyan]")
                elif 'Downloading' in line or 'Get:' in line:
                    progress.update(task, description=f"[cyan]Downloading...[/cyan]")
                elif 'Installing' in line:
                    progress.update(task, description=f"[cyan]Installing...[/cyan]")

            process.wait(timeout=timeout)

            # Mark complete
            progress.update(task, completed=100)

            return {
                'success': process.returncode == 0,
                'stdout': ''.join(stdout_lines),
                'stderr': '',
                'returncode': process.returncode
            }

        except subprocess.TimeoutExpired:
            process.kill()
            return {
                'success': False,
                'stdout': ''.join(stdout_lines),
                'stderr': 'Command timed out',
                'returncode': -1
            }
        except Exception as e:
            return {
                'success': False,
                'stdout': ''.join(stdout_lines),
                'stderr': str(e),
                'returncode': -1
            }


def multi_step_progress(steps):
    """Run multiple steps with an overall progress bar

    Args:
        steps: List of dicts with {name: str, command: str|list, optional: bool}

    Returns:
        dict: {success: bool, results: list, failed_step: str|None}
    """
    results = []
    failed_step = None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        overall_task = progress.add_task("[bold cyan]Overall Progress", total=len(steps))

        for i, step in enumerate(steps):
            step_name = step.get('name', f'Step {i+1}')
            command = step.get('command')
            optional = step.get('optional', False)
            shell = step.get('shell', False)

            progress.update(overall_task, description=f"[cyan]{step_name}[/cyan]")

            try:
                if isinstance(command, str) and not shell:
                    command = command.split()

                process = subprocess.Popen(
                    command,
                    shell=shell,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                stdout, stderr = process.communicate(timeout=300)

                result = {
                    'step': step_name,
                    'success': process.returncode == 0,
                    'stdout': stdout,
                    'stderr': stderr
                }
                results.append(result)

                if not result['success'] and not optional:
                    failed_step = step_name
                    progress.update(overall_task, completed=i+1)
                    console.print(f"\n[red]Step failed: {step_name}[/red]")
                    if stderr:
                        console.print(f"[dim]{stderr[:200]}...[/dim]" if len(stderr) > 200 else f"[dim]{stderr}[/dim]")
                    return {
                        'success': False,
                        'results': results,
                        'failed_step': failed_step
                    }

            except Exception as e:
                result = {
                    'step': step_name,
                    'success': False,
                    'stdout': '',
                    'stderr': str(e)
                }
                results.append(result)

                if not optional:
                    failed_step = step_name
                    return {
                        'success': False,
                        'results': results,
                        'failed_step': failed_step
                    }

            progress.update(overall_task, completed=i+1)

    return {
        'success': True,
        'results': results,
        'failed_step': None
    }


class InstallProgress:
    """Context manager for installation progress tracking"""

    def __init__(self, total_steps, description="Installing"):
        self.total_steps = total_steps
        self.description = description
        self.current_step = 0
        self.progress = None
        self.task = None

    def __enter__(self):
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console
        )
        self.progress.start()
        self.task = self.progress.add_task(f"[cyan]{self.description}[/cyan]", total=self.total_steps)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.progress.stop()
        return False

    def advance(self, step_name=None):
        """Advance progress by one step"""
        self.current_step += 1
        if step_name:
            self.progress.update(self.task, completed=self.current_step, description=f"[cyan]{step_name}[/cyan]")
        else:
            self.progress.update(self.task, completed=self.current_step)

    def update_description(self, description):
        """Update the current step description"""
        self.progress.update(self.task, description=f"[cyan]{description}[/cyan]")

    def complete(self):
        """Mark as complete"""
        self.progress.update(self.task, completed=self.total_steps)
