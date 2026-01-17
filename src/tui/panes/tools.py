"""Tools Pane - System diagnostics and tools."""

import asyncio
import logging
import socket
import struct
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static, Button, Log, Rule
from textual import work

logger = logging.getLogger('tui')


class ToolsPane(Container):
    """System Tools pane - Network, RF, MUDP"""

    def compose(self) -> ComposeResult:
        yield Static("# System Tools", classes="title")
        yield Rule()

        yield Static("## Network Tools", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Ping Test", id="tool-ping")
            yield Button("Port 4403", id="tool-port")
            yield Button("Interfaces", id="tool-ifaces")
            yield Button("Find Devices", id="tool-scan")

        yield Static("## Network Diagnostics", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("UDP Listeners", id="tool-udp")
            yield Button("TCP Listeners", id="tool-tcp")
            yield Button("RNS Ports", id="tool-rns-ports")
            yield Button("Mesh Ports", id="tool-mesh-ports")
        with Horizontal(classes="button-row"):
            yield Button("Kill Clients", id="tool-kill-clients", variant="error")
            yield Button("Stop RNS", id="tool-stop-rns", variant="error")
            yield Button("Full Diag", id="tool-full-diag", variant="primary")

        yield Static("## RF Tools", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("LoRa Presets", id="tool-presets")
            yield Button("Detect Radio", id="tool-radio")
            yield Button("SPI/GPIO", id="tool-spi")

        yield Static("## MUDP Tools", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("MUDP Status", id="tool-mudp-status")
            yield Button("Install MUDP", id="tool-mudp-install")
            yield Button("Multicast Test", id="tool-multicast")

        yield Static("## Output", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Clear", id="tool-clear")
            yield Button("Refresh", id="tool-refresh")

        yield Log(id="tool-output", classes="log-panel")

    async def on_mount(self):
        """Called when widget is mounted"""
        self._refresh_status()

    def _refresh_status(self):
        """Refresh tool status"""
        output = self.query_one("#tool-output", Log)
        output.clear()
        output.write("[cyan]System Tools Ready[/cyan]")
        output.write("Select a tool above to run diagnostics")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        logger.info(f"[Tools] Button pressed: {button_id}")
        output = self.query_one("#tool-output", Log)

        if button_id == "tool-clear":
            output.clear()
        elif button_id == "tool-refresh":
            self._refresh_status()
        elif button_id == "tool-ping":
            self._run_ping(output)
        elif button_id == "tool-port":
            self._test_port(output)
        elif button_id == "tool-ifaces":
            self._show_interfaces(output)
        elif button_id == "tool-scan":
            self._scan_devices(output)
        elif button_id == "tool-presets":
            self._show_presets(output)
        elif button_id == "tool-radio":
            self._detect_radio(output)
        elif button_id == "tool-spi":
            self._check_spi(output)
        elif button_id == "tool-mudp-status":
            self._mudp_status(output)
        elif button_id == "tool-mudp-install":
            self._install_mudp(output)
        elif button_id == "tool-multicast":
            self._test_multicast(output)
        elif button_id == "tool-udp":
            self._show_udp_listeners(output)
        elif button_id == "tool-tcp":
            self._show_tcp_listeners(output)
        elif button_id == "tool-rns-ports":
            self._check_rns_ports(output)
        elif button_id == "tool-mesh-ports":
            self._check_mesh_ports(output)
        elif button_id == "tool-kill-clients":
            self._kill_clients(output)
        elif button_id == "tool-stop-rns":
            self._stop_rns(output)
        elif button_id == "tool-full-diag":
            self._full_diagnostics(output)

    @work
    async def _run_ping(self, output: Log):
        """Run ping test"""
        output.write("\n[cyan]Pinging 8.8.8.8...[/cyan]")
        try:
            result = await asyncio.create_subprocess_exec(
                'ping', '-c', '4', '8.8.8.8',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            output.write(stdout.decode())
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    @work
    async def _test_port(self, output: Log):
        """Test Meshtastic TCP port"""
        output.write("\n[cyan]Testing port 4403...[/cyan]")
        for host in ['127.0.0.1', 'localhost']:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex((host, 4403))
                status = "[green]OPEN[/green]" if result == 0 else "[red]CLOSED[/red]"
                output.write(f"  {host}:4403 - {status}")
            except Exception as e:
                output.write(f"  {host}:4403 - [red]Error: {e}[/red]")
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

    @work
    async def _show_interfaces(self, output: Log):
        """Show network interfaces"""
        output.write("\n[cyan]Network Interfaces:[/cyan]")
        try:
            result = await asyncio.create_subprocess_exec(
                'ip', '-br', 'addr',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            output.write(stdout.decode())
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    @work
    async def _scan_devices(self, output: Log):
        """Scan for Meshtastic devices"""
        output.write("\n[cyan]Scanning for Meshtastic devices (port 4403)...[/cyan]")
        logger.info("Starting device scan")

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            base = '.'.join(local_ip.split('.')[:3])

            found = []

            async def check_host(ip: str):
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip, 4403),
                        timeout=0.5
                    )
                    writer.close()
                    await writer.wait_closed()
                    return ip
                except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
                    return None

            output.write(f"  Scanning {base}.1-254 ...")
            logger.debug(f"Scanning subnet {base}.0/24")

            batch_size = 50
            for batch_start in range(1, 255, batch_size):
                batch_end = min(batch_start + batch_size, 255)
                tasks = [check_host(f"{base}.{i}") for i in range(batch_start, batch_end)]
                results = await asyncio.gather(*tasks)

                for ip in results:
                    if ip:
                        output.write(f"  [green]Found: {ip}:4403[/green]")
                        found.append(ip)
                        logger.info(f"Found Meshtastic device at {ip}:4403")

                progress = (batch_end / 254) * 100
                output.write(f"  Progress: {progress:.0f}%")

            output.write(f"\n[cyan]Scan complete. Found {len(found)} device(s)[/cyan]")
            logger.info(f"Scan complete: found {len(found)} devices")

        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")
            logger.error(f"Device scan error: {e}")

    def _show_presets(self, output: Log):
        """Show LoRa presets"""
        output.write("\n[cyan]LoRa Modem Presets:[/cyan]")
        presets = [
            ("SHORT_TURBO", "21875 bps", "-108 dBm", "~3 km"),
            ("SHORT_FAST", "10937 bps", "-111 dBm", "~5 km"),
            ("MEDIUM_FAST", "3516 bps", "-117 dBm", "~12 km"),
            ("LONG_FAST", "1066 bps", "-123 dBm", "~30 km"),
            ("LONG_SLOW", "293 bps", "-129 dBm", "~80 km"),
            ("VERY_LONG_SLOW", "146 bps", "-132 dBm", "~120 km"),
        ]
        for name, rate, sens, range_ in presets:
            output.write(f"  {name}: {rate}, {sens}, {range_}")

    @work
    async def _detect_radio(self, output: Log):
        """Detect LoRa radio"""
        output.write("\n[cyan]Detecting LoRa Radio...[/cyan]")
        spi = list(Path('/dev').glob('spidev*'))
        if spi:
            output.write(f"  [green]SPI devices: {len(spi)}[/green]")
            for d in spi:
                output.write(f"    {d}")
        else:
            output.write("  [red]No SPI devices found[/red]")

    @work
    async def _check_spi(self, output: Log):
        """Check SPI/GPIO status"""
        output.write("\n[cyan]SPI/GPIO Status:[/cyan]")

        spi = Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists()
        output.write(f"  SPI: {'[green]Enabled[/green]' if spi else '[red]Disabled[/red]'}")

        i2c = Path('/dev/i2c-1').exists()
        output.write(f"  I2C: {'[green]Enabled[/green]' if i2c else '[yellow]Disabled[/yellow]'}")

        gpio = Path('/sys/class/gpio').exists()
        output.write(f"  GPIO: {'[green]Available[/green]' if gpio else '[red]Not available[/red]'}")

    @work
    async def _mudp_status(self, output: Log):
        """Check MUDP status"""
        output.write("\n[cyan]MUDP Status:[/cyan]")
        try:
            result = await asyncio.create_subprocess_exec(
                'pip', 'show', 'mudp',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            if result.returncode == 0:
                for line in stdout.decode().split('\n'):
                    if line.startswith(('Name:', 'Version:')):
                        output.write(f"  {line}")
                output.write("  [green]MUDP is installed[/green]")
            else:
                output.write("  [yellow]MUDP not installed[/yellow]")
                output.write("  Install with: pip install mudp")
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    @work
    async def _install_mudp(self, output: Log):
        """Install MUDP"""
        output.write("\n[cyan]Installing MUDP...[/cyan]")
        try:
            result = await asyncio.create_subprocess_exec(
                'pip', 'install', '--upgrade', '--break-system-packages', 'mudp',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            if result.returncode == 0:
                output.write("[green]MUDP installed successfully![/green]")
            else:
                output.write(f"[red]Install failed: {stderr.decode()}[/red]")
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    @work
    async def _test_multicast(self, output: Log):
        """Test multicast join"""
        output.write("\n[cyan]Testing multicast group 224.0.0.69...[/cyan]")
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', 4403))
            mreq = struct.pack("4sl", socket.inet_aton("224.0.0.69"), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            output.write("  [green]Joined multicast group successfully[/green]")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            output.write("  [green]Left multicast group[/green]")
        except OSError as e:
            if "Address already in use" in str(e):
                output.write("  [yellow]Port 4403 in use (meshtasticd running?) - OK[/yellow]")
            else:
                output.write(f"  [red]Error: {e}[/red]")
        except Exception as e:
            output.write(f"  [red]Error: {e}[/red]")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def _parse_proc_net(self, protocol: str) -> list:
        """Parse /proc/net/udp or /proc/net/tcp"""
        results = []
        try:
            with open(f"/proc/net/{protocol}", 'r') as f:
                lines = f.readlines()[1:]
            for line in lines:
                parts = line.split()
                if len(parts) >= 10:
                    addr_parts = parts[1].split(':')
                    try:
                        ip_int = int(addr_parts[0], 16)
                        ip_bytes = [(ip_int >> i) & 0xFF for i in (0, 8, 16, 24)]
                        ip_str = '.'.join(str(b) for b in ip_bytes)
                        port = int(addr_parts[1], 16)
                        state_names = {'01': 'ESTABLISHED', '0A': 'LISTEN', '06': 'TIME_WAIT'}
                        results.append({
                            'ip': ip_str, 'port': port,
                            'state': state_names.get(parts[3].upper(), parts[3])
                        })
                    except (ValueError, IndexError):
                        continue
        except (FileNotFoundError, PermissionError):
            pass
        return results

    @work
    async def _show_udp_listeners(self, output: Log):
        """Show UDP listeners"""
        output.write("\n[cyan]UDP Listeners[/cyan]")
        entries = self._parse_proc_net('udp')
        output.write(f"{'IP':>15} : {'Port':>5}")
        output.write("-" * 25)
        for e in entries:
            if e['port'] != 0:
                output.write(f"{e['ip']:>15} : {e['port']:>5}")
        output.write(f"\nTotal: {len([e for e in entries if e['port'] != 0])} sockets")

    @work
    async def _show_tcp_listeners(self, output: Log):
        """Show TCP listeners"""
        output.write("\n[cyan]TCP Listeners[/cyan]")
        entries = self._parse_proc_net('tcp')
        listen = [e for e in entries if e['state'] == 'LISTEN']
        output.write(f"{'IP':>15} : {'Port':>5}  State")
        output.write("-" * 35)
        for e in listen:
            output.write(f"{e['ip']:>15} : {e['port']:>5}  {e['state']}")
        output.write(f"\nTotal: {len(listen)} listening")

    @work
    async def _check_rns_ports(self, output: Log):
        """Check RNS port 29716"""
        output.write("\n[cyan]RNS Port Check (29716)[/cyan]")
        entries = self._parse_proc_net('udp')
        found = [e for e in entries if e['port'] == 29716]
        if found:
            output.write("  [red]✗ Port 29716 IN USE[/red]")
        else:
            output.write("  [green]✓ Port 29716 FREE[/green]")
        try:
            result = await asyncio.create_subprocess_exec(
                'pgrep', '-a', '-f', 'rnsd|nomadnet|lxmf',
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            if stdout:
                output.write("\nRNS Processes:")
                for line in stdout.decode().strip().split('\n'):
                    output.write(f"  {line}")
            else:
                output.write("\n  No RNS processes running")
        except Exception:
            pass

    @work
    async def _check_mesh_ports(self, output: Log):
        """Check Meshtastic ports"""
        output.write("\n[cyan]Meshtastic Port Check[/cyan]")
        tcp = self._parse_proc_net('tcp')
        for port in [4403, 9443]:
            found = [e for e in tcp if e['port'] == port and e['state'] == 'LISTEN']
            if found:
                output.write(f"  [green]✓ TCP {port} LISTENING[/green]")
            else:
                output.write(f"  [red]✗ TCP {port} NOT listening[/red]")

    @work
    async def _kill_clients(self, output: Log):
        """Kill competing clients"""
        output.write("\n[cyan]Killing competing clients...[/cyan]")
        killed = []
        for pattern in ['nomadnet', 'lxmf']:
            try:
                result = await asyncio.create_subprocess_exec(
                    'pkill', '-9', '-f', pattern,
                    stdout=asyncio.subprocess.PIPE
                )
                await result.communicate()
                if result.returncode == 0:
                    killed.append(pattern)
            except Exception:
                pass
        if killed:
            output.write(f"  [green]Killed: {', '.join(killed)}[/green]")
        else:
            output.write("  [yellow]No clients found[/yellow]")

    @work
    async def _stop_rns(self, output: Log):
        """Stop all RNS processes"""
        output.write("\n[cyan]Stopping all RNS processes...[/cyan]")
        killed = []
        for proc in ['rnsd', 'nomadnet', 'lxmf', 'RNS']:
            try:
                result = await asyncio.create_subprocess_exec(
                    'pkill', '-9', '-f', proc,
                    stdout=asyncio.subprocess.PIPE
                )
                await result.communicate()
                if result.returncode == 0:
                    killed.append(proc)
            except Exception:
                pass
        if killed:
            output.write(f"  [green]Killed: {', '.join(killed)}[/green]")
        else:
            output.write("  [yellow]No RNS processes found[/yellow]")

    @work
    async def _full_diagnostics(self, output: Log):
        """Run full network diagnostics"""
        output.write("\n[cyan]" + "=" * 40 + "[/cyan]")
        output.write("[cyan]FULL NETWORK DIAGNOSTICS[/cyan]")
        output.write("[cyan]" + "=" * 40 + "[/cyan]\n")
        await self._show_udp_listeners(output)
        await self._show_tcp_listeners(output)
        await self._check_rns_ports(output)
        await self._check_mesh_ports(output)
        output.write("\n[cyan]" + "=" * 40 + "[/cyan]")
        output.write("[green]DIAGNOSTICS COMPLETE[/green]")
