#!/bin/bash
#
# MeshForge LoRa Configuration Helper
#
# Configures essential LoRa settings to join a mesh network:
#   - Region (required for radio to transmit)
#   - Channel/Frequency Slot (must match network)
#   - Modem Preset (must match network)
#   - TX Power
#   - Hop Limit
#
# Usage:
#   sudo bash scripts/configure_lora.sh                    # Interactive
#   sudo bash scripts/configure_lora.sh --region US        # Set region only
#   sudo bash scripts/configure_lora.sh --profile default  # Use preset profile
#
# Requires meshtasticd running on localhost:4403
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Defaults
HOST="localhost"
REGION=""
CHANNEL=""
PRESET=""
TX_POWER=""
HOP_LIMIT=""
PROFILE=""

# Network profiles (common configurations)
declare -A PROFILES
PROFILES["default"]="region=US,channel=20,preset=LONG_FAST,tx_power=22,hop_limit=3"
PROFILES["us_default"]="region=US,channel=20,preset=LONG_FAST,tx_power=22,hop_limit=3"
PROFILES["us_longrange"]="region=US,channel=20,preset=VERY_LONG_SLOW,tx_power=30,hop_limit=7"
PROFILES["us_fast"]="region=US,channel=20,preset=SHORT_FAST,tx_power=22,hop_limit=3"
PROFILES["eu_default"]="region=EU_868,channel=1,preset=LONG_FAST,tx_power=14,hop_limit=3"
PROFILES["au_default"]="region=AU_915,channel=20,preset=LONG_FAST,tx_power=22,hop_limit=3"

# Meshtastic modem presets
PRESETS=(
    "LONG_FAST"
    "LONG_SLOW"
    "LONG_MODERATE"
    "VERY_LONG_SLOW"
    "SHORT_FAST"
    "SHORT_SLOW"
    "MEDIUM_FAST"
    "MEDIUM_SLOW"
)

# Region codes
REGIONS=(
    "US"
    "EU_868"
    "EU_433"
    "AU_915"
    "CN"
    "JP"
    "KR"
    "TW"
    "IN"
    "NZ_865"
    "RU"
    "TH"
    "LORA_24"
    "UA_868"
    "UA_433"
)

print_header() {
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║        MeshForge LoRa Network Configuration               ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --host HOST         Meshtasticd host (default: localhost)"
    echo "  --region REGION     LoRa region (US, EU_868, AU_915, etc.)"
    echo "  --channel NUM       Frequency slot/channel number (0-104)"
    echo "  --preset PRESET     Modem preset (LONG_FAST, SHORT_FAST, etc.)"
    echo "  --tx-power DBM      Transmit power in dBm (1-30)"
    echo "  --hop-limit NUM     Maximum hops (1-7)"
    echo "  --profile NAME      Use preset profile (default, us_longrange, etc.)"
    echo "  --list-profiles     Show available profiles"
    echo "  --list-presets      Show available modem presets"
    echo "  --list-regions      Show available regions"
    echo "  --show              Show current LoRa configuration"
    echo "  --interactive       Run interactive configuration"
    echo "  -h, --help          Show this help"
    echo ""
    echo "Examples:"
    echo "  $0 --region US --channel 20 --preset LONG_FAST"
    echo "  $0 --profile us_default"
    echo "  $0 --interactive"
}

check_meshtasticd() {
    # Check if meshtasticd is running
    if ! nc -z "$HOST" 4403 2>/dev/null; then
        echo -e "${RED}Error: Cannot connect to meshtasticd at ${HOST}:4403${NC}"
        echo -e "${YELLOW}Make sure meshtasticd is running:${NC}"
        echo -e "  sudo systemctl start meshtasticd"
        echo -e "  sudo systemctl status meshtasticd"
        exit 1
    fi
}

run_meshtastic() {
    # Run meshtastic CLI command
    meshtastic --host "$HOST" "$@"
}

show_current_config() {
    echo -e "${CYAN}Current LoRa Configuration:${NC}"
    echo ""
    run_meshtastic --get lora 2>/dev/null || {
        echo -e "${RED}Failed to get configuration${NC}"
        return 1
    }
}

apply_profile() {
    local profile_name="$1"
    local profile_data="${PROFILES[$profile_name]}"

    if [[ -z "$profile_data" ]]; then
        echo -e "${RED}Unknown profile: $profile_name${NC}"
        echo "Available profiles:"
        for p in "${!PROFILES[@]}"; do
            echo "  - $p"
        done
        exit 1
    fi

    echo -e "${CYAN}Applying profile: ${BOLD}$profile_name${NC}"
    echo -e "  Settings: $profile_data"
    echo ""

    # Parse profile data
    IFS=',' read -ra SETTINGS <<< "$profile_data"
    for setting in "${SETTINGS[@]}"; do
        key="${setting%%=*}"
        value="${setting#*=}"
        case "$key" in
            region)    REGION="$value" ;;
            channel)   CHANNEL="$value" ;;
            preset)    PRESET="$value" ;;
            tx_power)  TX_POWER="$value" ;;
            hop_limit) HOP_LIMIT="$value" ;;
        esac
    done
}

apply_settings() {
    local changes_made=false

    echo -e "${CYAN}Applying LoRa settings...${NC}"
    echo ""

    if [[ -n "$REGION" ]]; then
        echo -e "  Setting region: ${BOLD}$REGION${NC}"
        run_meshtastic --set lora.region "$REGION" && changes_made=true
    fi

    if [[ -n "$CHANNEL" ]]; then
        echo -e "  Setting channel: ${BOLD}$CHANNEL${NC}"
        run_meshtastic --set lora.channel_num "$CHANNEL" && changes_made=true
    fi

    if [[ -n "$PRESET" ]]; then
        echo -e "  Setting modem preset: ${BOLD}$PRESET${NC}"
        run_meshtastic --set lora.modem_preset "$PRESET" && changes_made=true
    fi

    if [[ -n "$TX_POWER" ]]; then
        echo -e "  Setting TX power: ${BOLD}${TX_POWER} dBm${NC}"
        run_meshtastic --set lora.tx_power "$TX_POWER" && changes_made=true
    fi

    if [[ -n "$HOP_LIMIT" ]]; then
        echo -e "  Setting hop limit: ${BOLD}$HOP_LIMIT${NC}"
        run_meshtastic --set lora.hop_limit "$HOP_LIMIT" && changes_made=true
    fi

    if $changes_made; then
        echo ""
        echo -e "${GREEN}Settings applied successfully!${NC}"
        echo -e "${YELLOW}Note: Radio will restart to apply changes${NC}"
    else
        echo -e "${YELLOW}No changes made${NC}"
    fi
}

interactive_config() {
    print_header
    check_meshtasticd

    echo -e "${CYAN}Current settings:${NC}"
    show_current_config
    echo ""

    # Region selection
    echo -e "${BOLD}Select Region:${NC}"
    PS3="Enter number (or 'q' to skip): "
    select region in "${REGIONS[@]}"; do
        if [[ "$REPLY" == "q" ]]; then
            break
        elif [[ -n "$region" ]]; then
            REGION="$region"
            echo -e "  Selected: ${GREEN}$REGION${NC}"
            break
        fi
    done
    echo ""

    # Channel input
    echo -e "${BOLD}Frequency Slot/Channel (0-104, default for region varies):${NC}"
    echo -e "  ${YELLOW}This MUST match your network's channel!${NC}"
    read -p "  Enter channel number (or press Enter to skip): " input
    if [[ -n "$input" ]]; then
        CHANNEL="$input"
        echo -e "  Selected: ${GREEN}$CHANNEL${NC}"
    fi
    echo ""

    # Modem preset selection
    echo -e "${BOLD}Select Modem Preset:${NC}"
    echo -e "  ${YELLOW}This MUST match your network's preset!${NC}"
    PS3="Enter number (or 'q' to skip): "
    select preset in "${PRESETS[@]}"; do
        if [[ "$REPLY" == "q" ]]; then
            break
        elif [[ -n "$preset" ]]; then
            PRESET="$preset"
            echo -e "  Selected: ${GREEN}$PRESET${NC}"
            break
        fi
    done
    echo ""

    # TX Power
    echo -e "${BOLD}Transmit Power (dBm):${NC}"
    echo -e "  US: max 30 dBm (1W), EU: max 14 dBm"
    read -p "  Enter TX power (or press Enter for default 22): " input
    if [[ -n "$input" ]]; then
        TX_POWER="$input"
    fi
    echo ""

    # Hop limit
    echo -e "${BOLD}Hop Limit (1-7):${NC}"
    read -p "  Enter hop limit (or press Enter for default 3): " input
    if [[ -n "$input" ]]; then
        HOP_LIMIT="$input"
    fi
    echo ""

    # Confirm
    echo -e "${CYAN}═══ Configuration Summary ═══${NC}"
    [[ -n "$REGION" ]] && echo -e "  Region:    ${BOLD}$REGION${NC}"
    [[ -n "$CHANNEL" ]] && echo -e "  Channel:   ${BOLD}$CHANNEL${NC}"
    [[ -n "$PRESET" ]] && echo -e "  Preset:    ${BOLD}$PRESET${NC}"
    [[ -n "$TX_POWER" ]] && echo -e "  TX Power:  ${BOLD}${TX_POWER} dBm${NC}"
    [[ -n "$HOP_LIMIT" ]] && echo -e "  Hop Limit: ${BOLD}$HOP_LIMIT${NC}"
    echo ""

    read -p "Apply these settings? [Y/n] " confirm
    if [[ ! "$confirm" =~ ^([nN][oO]|[nN])$ ]]; then
        apply_settings
    else
        echo -e "${YELLOW}Configuration cancelled${NC}"
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            HOST="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --channel)
            CHANNEL="$2"
            shift 2
            ;;
        --preset)
            PRESET="$2"
            shift 2
            ;;
        --tx-power)
            TX_POWER="$2"
            shift 2
            ;;
        --hop-limit)
            HOP_LIMIT="$2"
            shift 2
            ;;
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --list-profiles)
            echo "Available profiles:"
            for p in "${!PROFILES[@]}"; do
                echo "  $p: ${PROFILES[$p]}"
            done
            exit 0
            ;;
        --list-presets)
            echo "Available modem presets:"
            for p in "${PRESETS[@]}"; do
                echo "  $p"
            done
            exit 0
            ;;
        --list-regions)
            echo "Available regions:"
            for r in "${REGIONS[@]}"; do
                echo "  $r"
            done
            exit 0
            ;;
        --show)
            check_meshtasticd
            show_current_config
            exit 0
            ;;
        --interactive)
            interactive_config
            exit 0
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            print_usage
            exit 1
            ;;
    esac
done

# If profile specified, apply it
if [[ -n "$PROFILE" ]]; then
    apply_profile "$PROFILE"
fi

# If any settings specified, apply them
if [[ -n "$REGION" || -n "$CHANNEL" || -n "$PRESET" || -n "$TX_POWER" || -n "$HOP_LIMIT" ]]; then
    print_header
    check_meshtasticd
    apply_settings
elif [[ -z "$PROFILE" ]]; then
    # No arguments - run interactive
    interactive_config
fi
