#!/bin/bash
# Verification script for install logic
# Run this to check our assumptions are correct before fresh install

# Don't exit on errors - we want to run all checks
set +e
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "═══════════════════════════════════════════════════════════"
echo "  MeshForge Install Logic Verification"
echo "═══════════════════════════════════════════════════════════"
echo ""

PASS=0
FAIL=0

check() {
    local name="$1"
    local result="$2"
    if [[ "$result" == "pass" ]]; then
        echo -e "  ${GREEN}✓${NC} $name"
        ((PASS++))
    else
        echo -e "  ${RED}✗${NC} $name"
        ((FAIL++))
    fi
}

# ─────────────────────────────────────────────────────────────────
echo "1. OS Detection"
echo "───────────────────────────────────────────────────────────"

if [[ -f /etc/os-release ]]; then
    source /etc/os-release
    echo "   ID: $ID"
    echo "   VERSION_ID: $VERSION_ID"
    echo "   VERSION_CODENAME: $VERSION_CODENAME"

    # Check Trixie detection
    if [[ "$VERSION_CODENAME" == "trixie" ]] || [[ "$VERSION_ID" == "13" ]]; then
        check "Trixie/Debian 13 detected" "pass"
        EXPECTED_REPO="Debian_13"
    elif [[ "$VERSION_CODENAME" == "bookworm" ]] || [[ "$VERSION_ID" == "12" ]]; then
        check "Bookworm/Debian 12 detected" "pass"
        EXPECTED_REPO="Debian_12"
    else
        check "OS detection" "pass"
        EXPECTED_REPO="Debian_12"
    fi
else
    check "/etc/os-release exists" "fail"
fi

# ─────────────────────────────────────────────────────────────────
echo ""
echo "2. Repository URL Check"
echo "───────────────────────────────────────────────────────────"

# Check beta repo has our OS
BETA_URL="https://download.opensuse.org/repositories/network:/Meshtastic:/beta/${EXPECTED_REPO}/"
echo "   Checking: $BETA_URL"
CURL_RESULT=$(curl -s --connect-timeout 5 --max-time 10 --head "$BETA_URL" 2>/dev/null | head -1 || echo "failed")
if echo "$CURL_RESULT" | grep -q "200\|301\|302"; then
    check "Beta repo accessible for $EXPECTED_REPO" "pass"
else
    check "Beta repo accessible for $EXPECTED_REPO" "fail"
    echo "   ${YELLOW}(Network issue or repo doesn't exist)${NC}"
fi

# ─────────────────────────────────────────────────────────────────
echo ""
echo "3. Install Script Logic"
echo "───────────────────────────────────────────────────────────"

INSTALL_SCRIPT="$(dirname "$0")/install_noc.sh"
if [[ -f "$INSTALL_SCRIPT" ]]; then
    # Check it doesn't unconditionally overwrite config.yaml
    if grep -q "if.*config.yaml.*exists.*Webserver" "$INSTALL_SCRIPT" || \
       grep -q "grep -q.*Webserver.*config.yaml" "$INSTALL_SCRIPT"; then
        check "Install script checks for existing valid config.yaml" "pass"
    else
        check "Install script checks for existing valid config.yaml" "fail"
        echo "   ${YELLOW}Script may overwrite meshtasticd's config.yaml${NC}"
    fi

    # Check it doesn't create HAT templates inline
    HAT_TEMPLATES=$(grep -c 'cat >.*available.d.*\.yaml' "$INSTALL_SCRIPT" 2>/dev/null)
    HAT_TEMPLATES=${HAT_TEMPLATES:-0}
    if [[ "$HAT_TEMPLATES" == "0" ]] || [[ -z "$HAT_TEMPLATES" ]]; then
        check "Install script doesn't create HAT templates" "pass"
    else
        check "Install script doesn't create HAT templates" "fail"
        echo "   ${YELLOW}Found $HAT_TEMPLATES inline HAT template creations${NC}"
    fi

    # Check it uses beta repo
    if grep -q "network:/Meshtastic:/beta\|network:Meshtastic:beta" "$INSTALL_SCRIPT"; then
        check "Install script uses beta repo (has Debian 13)" "pass"
    else
        check "Install script uses beta repo" "fail"
    fi
else
    echo "   ${YELLOW}Install script not found at $INSTALL_SCRIPT${NC}"
fi

# ─────────────────────────────────────────────────────────────────
echo ""
echo "4. TUI Logic Check"
echo "───────────────────────────────────────────────────────────"

TUI_MAIN="$(dirname "$0")/../src/launcher_tui/main.py"
if [[ -f "$TUI_MAIN" ]]; then
    # Check _fix_spi_config doesn't create waveshare template
    if grep -A50 "_fix_spi_config" "$TUI_MAIN" | grep -q "waveshare.*write_text"; then
        check "TUI _fix_spi_config doesn't create HAT templates" "fail"
    else
        check "TUI _fix_spi_config doesn't create HAT templates" "pass"
    fi

    # Check it checks for Webserver before overwriting
    if grep -q "Webserver.*in.*read_text\|'Webserver:'.*config_yaml" "$TUI_MAIN"; then
        check "TUI checks for valid config before modifying" "pass"
    else
        check "TUI checks for valid config before modifying" "fail"
    fi
else
    echo "   ${YELLOW}TUI main.py not found${NC}"
fi

# ─────────────────────────────────────────────────────────────────
echo ""
echo "5. Template Files"
echo "───────────────────────────────────────────────────────────"

TEMPLATES_DIR="$(dirname "$0")/../templates"
if [[ -d "$TEMPLATES_DIR" ]]; then
    # Check config.yaml is minimal
    if [[ -f "$TEMPLATES_DIR/config.yaml" ]]; then
        LINES=$(wc -l < "$TEMPLATES_DIR/config.yaml")
        if [[ "$LINES" -lt 30 ]]; then
            check "templates/config.yaml is minimal ($LINES lines)" "pass"
        else
            check "templates/config.yaml is minimal" "fail"
            echo "   ${YELLOW}Has $LINES lines - may have extra content${NC}"
        fi

        # Check it doesn't have Bandwidth/SpreadFactor
        if grep -q "Bandwidth:\|SpreadFactor:\|TXpower:" "$TEMPLATES_DIR/config.yaml"; then
            check "templates/config.yaml has NO radio parameters" "fail"
        else
            check "templates/config.yaml has NO radio parameters" "pass"
        fi
    fi

    # Check available.d doesn't have HAT configs
    if [[ -d "$TEMPLATES_DIR/available.d" ]]; then
        HAT_FILES=$(ls "$TEMPLATES_DIR/available.d/"*hat*.yaml 2>/dev/null | wc -l || echo "0")
        if [[ "$HAT_FILES" -eq 0 ]]; then
            check "templates/available.d has NO HAT configs (meshtasticd provides)" "pass"
        else
            check "templates/available.d has NO HAT configs" "fail"
        fi
    else
        check "templates/available.d removed or renamed" "pass"
    fi
else
    echo "   ${YELLOW}Templates dir not found${NC}"
fi

# ─────────────────────────────────────────────────────────────────
echo ""
echo "6. Current System State"
echo "───────────────────────────────────────────────────────────"

# Check meshtasticd config if exists
if [[ -f /etc/meshtasticd/config.yaml ]]; then
    echo "   Found /etc/meshtasticd/config.yaml"

    if grep -q "Webserver:" /etc/meshtasticd/config.yaml; then
        check "config.yaml has Webserver section" "pass"
    else
        check "config.yaml has Webserver section" "fail"
        echo "   ${YELLOW}Web client won't work without Webserver section${NC}"
    fi

    if grep -q "Bandwidth:\|SpreadFactor:" /etc/meshtasticd/config.yaml; then
        check "config.yaml has NO radio params (they belong in device db)" "fail"
        echo "   ${YELLOW}Radio params in config.yaml - this is WRONG${NC}"
    else
        check "config.yaml has NO radio params" "pass"
    fi
fi

if [[ -d /etc/meshtasticd/available.d ]]; then
    AVAIL_COUNT=$(ls /etc/meshtasticd/available.d/*.yaml 2>/dev/null | wc -l)
    echo "   Found $AVAIL_COUNT templates in available.d/"
    if [[ "$AVAIL_COUNT" -gt 5 ]]; then
        check "meshtasticd provided HAT templates" "pass"
    else
        check "meshtasticd HAT templates present" "fail"
    fi
fi

if [[ -d /etc/meshtasticd/config.d ]]; then
    ACTIVE_COUNT=$(ls /etc/meshtasticd/config.d/*.yaml 2>/dev/null | wc -l)
    echo "   Found $ACTIVE_COUNT active configs in config.d/"
fi

# ─────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo -e "  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}"
echo "═══════════════════════════════════════════════════════════"

if [[ "$FAIL" -gt 0 ]]; then
    echo ""
    echo -e "${YELLOW}Some checks failed. Review above and fix before fresh install.${NC}"
    exit 1
else
    echo ""
    echo -e "${GREEN}All checks passed! Ready for fresh install.${NC}"
    exit 0
fi
