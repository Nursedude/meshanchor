# Meshtasticd Interactive Installer & Manager

An interactive installer, updater, and configuration tool for meshtasticd on Raspberry Pi OS.

## Features

- **Interactive Installation**: Guided setup for meshtasticd daemon
- **Version Management**: Install/update stable or beta versions
- **OS Detection**: Automatic detection of 32-bit/64-bit Raspberry Pi OS
- **Configuration**: Interactive LoRa and device configuration
- **Dependency Management**: Automatically fix deprecated dependencies
- **Error Handling**: Comprehensive debugging and troubleshooting tools
- **Hardware Detection**: Detect and configure LoRa radio modules (SPI/USB)

## Supported Platforms

- Raspberry Pi OS (32-bit armhf)
- Raspberry Pi OS (64-bit arm64)
- Raspbian Bookworm and newer

## Supported Hardware

- Raspberry Pi Zero 2W, 3, 4, Pi 400, Pi 5
- SPI LoRa HATs (MeshAdv-Pi, Adafruit RFM9x, Elecrow LoRa RFM95)
- USB LoRa modules

## Installation

```bash
git clone https://github.com/Nursedude/Meshtasticd_interactive_IU.git
cd Meshtasticd_interactive_IU
sudo python3 -m pip install -r requirements.txt
sudo python3 src/main.py
```

## Usage

### Interactive Mode
```bash
sudo python3 src/main.py
```

### Command Line Options
```bash
# Install latest stable version
sudo python3 src/main.py --install stable

# Install beta version
sudo python3 src/main.py --install beta

# Update existing installation
sudo python3 src/main.py --update

# Configure device
sudo python3 src/main.py --configure

# Check system and dependencies
sudo python3 src/main.py --check

# Debug mode
sudo python3 src/main.py --debug
```

## Requirements

- Python 3.7+
- Root/sudo access (for GPIO, SPI, and system package management)
- Internet connection (for downloading packages)

## Project Structure

```
Meshtasticd_interactive_IU/
├── src/
│   ├── main.py                 # Main entry point
│   ├── installer/
│   │   ├── __init__.py
│   │   ├── meshtasticd.py     # Meshtasticd installation logic
│   │   ├── dependencies.py     # Dependency management
│   │   └── version.py          # Version management
│   ├── config/
│   │   ├── __init__.py
│   │   ├── lora.py            # LoRa configuration
│   │   ├── device.py          # Device configuration
│   │   └── hardware.py        # Hardware detection
│   └── utils/
│       ├── __init__.py
│       ├── system.py          # System utilities
│       ├── logger.py          # Logging and debugging
│       └── cli.py             # CLI interface
├── scripts/
│   ├── install_armhf.sh       # 32-bit installation script
│   ├── install_arm64.sh       # 64-bit installation script
│   └── setup_permissions.sh   # GPIO/SPI permissions setup
├── tests/
├── docs/
├── requirements.txt
└── README.md
```

## License

GPL-3.0 (inherited from meshtastic/python)

## Contributing

Contributions welcome! Please open an issue or PR.

## Resources

- [Meshtastic Documentation](https://meshtastic.org/docs/)
- [Meshtastic Python Library](https://github.com/meshtastic/python)
- [LoRa Configuration](https://meshtastic.org/docs/configuration/radio/lora/)
