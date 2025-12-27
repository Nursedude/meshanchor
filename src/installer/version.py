"""Version management for meshtasticd"""

import requests
from packaging import version
from rich.console import Console
from rich.table import Table

from utils.system import run_command
from utils.logger import log, log_exception

console = Console()


class VersionManager:
    """Manage meshtasticd versions"""

    def __init__(self):
        self.github_api_url = "https://api.github.com/repos/meshtastic/firmware/releases"
        self.current_version = None

    def get_installed_version(self):
        """Get currently installed version"""
        if self.current_version:
            return self.current_version

        result = run_command('meshtasticd --version')

        if result['success']:
            # Parse version from output
            version_str = result['stdout'].strip()
            # Extract version number (format may vary)
            # Example: "meshtasticd v2.3.4" or "2.3.4"
            parts = version_str.split()
            for part in parts:
                if part.startswith('v'):
                    part = part[1:]  # Remove 'v' prefix
                try:
                    # Validate version format
                    version.parse(part)
                    self.current_version = part
                    return part
                except Exception:
                    continue

        return None

    def get_available_versions(self, include_beta=False):
        """Get available versions from GitHub releases"""
        log("Fetching available versions from GitHub")

        try:
            response = requests.get(self.github_api_url, timeout=10)
            response.raise_for_status()

            releases = response.json()
            versions = []

            for release in releases:
                tag_name = release.get('tag_name', '')
                is_prerelease = release.get('prerelease', False)
                is_draft = release.get('draft', False)

                # Skip drafts
                if is_draft:
                    continue

                # Skip prereleases unless requested
                if is_prerelease and not include_beta:
                    continue

                versions.append({
                    'version': tag_name,
                    'name': release.get('name', tag_name),
                    'prerelease': is_prerelease,
                    'published_at': release.get('published_at', ''),
                    'url': release.get('html_url', '')
                })

            return versions

        except requests.RequestException as e:
            log_exception(e, "Failed to fetch versions from GitHub")
            console.print(f"[yellow]Could not fetch versions from GitHub: {str(e)}[/yellow]")
            return []

    def get_latest_version(self, include_beta=False):
        """Get the latest version"""
        versions = self.get_available_versions(include_beta=include_beta)

        if not versions:
            return None

        # Filter by prerelease status
        if not include_beta:
            versions = [v for v in versions if not v['prerelease']]

        if not versions:
            return None

        # Return the first one (GitHub API returns them sorted by date)
        return versions[0]

    def check_for_updates(self):
        """Check if an update is available"""
        current = self.get_installed_version()

        if not current:
            console.print("[yellow]Could not determine installed version[/yellow]")
            return None

        latest = self.get_latest_version()

        if not latest:
            console.print("[yellow]Could not determine latest version[/yellow]")
            return None

        try:
            current_ver = version.parse(current.lstrip('v'))
            latest_ver = version.parse(latest['version'].lstrip('v'))

            if latest_ver > current_ver:
                return {
                    'update_available': True,
                    'current': current,
                    'latest': latest['version'],
                    'release_info': latest
                }
            else:
                return {
                    'update_available': False,
                    'current': current,
                    'latest': latest['version']
                }

        except Exception as e:
            log_exception(e, "Version comparison")
            return None

    def show_version_info(self):
        """Display version information"""
        console.print("\n[bold cyan]Version Information[/bold cyan]\n")

        current = self.get_installed_version()

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Type", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Status", style="yellow")

        if current:
            table.add_row("Installed", current, "")
        else:
            table.add_row("Installed", "Not found", "[red]Not installed[/red]")

        latest = self.get_latest_version()
        if latest:
            table.add_row("Latest Stable", latest['version'], "")

        latest_beta = self.get_latest_version(include_beta=True)
        if latest_beta and latest_beta.get('prerelease'):
            table.add_row("Latest Beta", latest_beta['version'], "[yellow]Prerelease[/yellow]")

        console.print(table)

        # Check for updates
        if current:
            update_info = self.check_for_updates()
            if update_info and update_info['update_available']:
                console.print(f"\n[bold green]Update available:[/bold green] {update_info['latest']}")
                console.print("Run with --update to upgrade")
            else:
                console.print("\n[green]You are running the latest version[/green]")

    def show_available_versions(self, include_beta=False):
        """Display all available versions"""
        console.print("\n[bold cyan]Available Versions[/bold cyan]\n")

        versions = self.get_available_versions(include_beta=include_beta)

        if not versions:
            console.print("[yellow]No versions found[/yellow]")
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Version", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Released", style="blue")

        for ver in versions[:10]:  # Show top 10
            ver_type = "Beta" if ver['prerelease'] else "Stable"
            released = ver['published_at'].split('T')[0] if ver['published_at'] else ''
            table.add_row(ver['version'], ver['name'], ver_type, released)

        console.print(table)
