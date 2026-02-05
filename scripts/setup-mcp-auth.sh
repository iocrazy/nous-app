#!/bin/bash

# ============================================
# Supabase MCP Authentication Setup Script
# ============================================
#
# Usage: ./setup-mcp-auth.sh [API_KEY]
#
# This script configures Kong Gateway to require
# API Key authentication for the /mcp endpoint.
#
# Files modified:
#   - .env (adds MCP_API_KEY)
#   - docker-compose.yml (passes MCP_API_KEY to Kong)
#   - volumes/api/kong.yml (adds consumer, ACL, plugins)
#
# ============================================

set -e

# Configuration
SUPABASE_DIR="${SUPABASE_DIR:-/Volumes/docker/datahub/sb-mediahub}"
KONG_YML="$SUPABASE_DIR/volumes/api/kong.yml"
ENV_FILE="$SUPABASE_DIR/.env"
COMPOSE_FILE="$SUPABASE_DIR/docker-compose.yml"

# Default API Key
DEFAULT_MCP_API_KEY="mcp_heygo_x7K9pL2mQ4vR8wY3"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_banner() {
    echo -e "${BLUE}"
    echo "╔══════════════════════════════════════════╗"
    echo "║   Supabase MCP Authentication Setup      ║"
    echo "╚══════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_banner

# Check if running in correct directory
if [ ! -f "$KONG_YML" ]; then
    echo -e "${RED}Error: kong.yml not found at $KONG_YML${NC}"
    echo "Set SUPABASE_DIR environment variable to your Supabase directory"
    exit 1
fi

# Get API Key from argument or prompt
if [ -n "$1" ]; then
    MCP_API_KEY="$1"
else
    read -p "Enter MCP API Key (press Enter for default): " MCP_API_KEY
    MCP_API_KEY=${MCP_API_KEY:-$DEFAULT_MCP_API_KEY}
fi

echo ""
echo -e "Using API Key: ${YELLOW}$MCP_API_KEY${NC}"
echo ""

# Backup files
echo -e "${BLUE}Creating backups...${NC}"
cp "$KONG_YML" "$KONG_YML.bak.$(date +%Y%m%d%H%M%S)"
cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
cp "$COMPOSE_FILE" "$COMPOSE_FILE.bak.$(date +%Y%m%d%H%M%S)"
echo -e "${GREEN}✓ Backups created${NC}"
echo ""

# Step 1: Update .env file
echo -e "${BLUE}[1/5] Updating .env file...${NC}"
if grep -q "^MCP_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/^MCP_API_KEY=.*/MCP_API_KEY=$MCP_API_KEY/" "$ENV_FILE"
    else
        sed -i "s/^MCP_API_KEY=.*/MCP_API_KEY=$MCP_API_KEY/" "$ENV_FILE"
    fi
    echo -e "${GREEN}✓ Updated MCP_API_KEY in .env${NC}"
else
    echo "MCP_API_KEY=$MCP_API_KEY" >> "$ENV_FILE"
    echo -e "${GREEN}✓ Added MCP_API_KEY to .env${NC}"
fi

# Step 2: Update docker-compose.yml
echo -e "${BLUE}[2/5] Updating docker-compose.yml...${NC}"
if grep -q "MCP_API_KEY:" "$COMPOSE_FILE" 2>/dev/null; then
    echo -e "${GREEN}✓ MCP_API_KEY already in docker-compose.yml${NC}"
else
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' '/DASHBOARD_PASSWORD:/a\
      MCP_API_KEY: ${MCP_API_KEY}' "$COMPOSE_FILE"
    else
        sed -i '/DASHBOARD_PASSWORD:/a\      MCP_API_KEY: ${MCP_API_KEY}' "$COMPOSE_FILE"
    fi
    echo -e "${GREEN}✓ Added MCP_API_KEY to docker-compose.yml${NC}"
fi

# Step 3: Add mcp-client consumer to kong.yml
echo -e "${BLUE}[3/5] Adding mcp-client consumer...${NC}"
if grep -q "username: mcp-client" "$KONG_YML" 2>/dev/null; then
    echo -e "${GREEN}✓ mcp-client consumer already exists${NC}"
else
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' '/- key: \$SUPABASE_SERVICE_KEY/a\
  - username: mcp-client\
    keyauth_credentials:\
      - key: $MCP_API_KEY' "$KONG_YML"
    else
        sed -i '/- key: \$SUPABASE_SERVICE_KEY/a\  - username: mcp-client\n    keyauth_credentials:\n      - key: $MCP_API_KEY' "$KONG_YML"
    fi
    echo -e "${GREEN}✓ Added mcp-client consumer${NC}"
fi

# Step 4: Add mcp ACL
echo -e "${BLUE}[4/5] Adding mcp ACL group...${NC}"
if grep -q "consumer: mcp-client" "$KONG_YML" 2>/dev/null; then
    echo -e "${GREEN}✓ mcp-client ACL already exists${NC}"
else
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' '/group: admin/a\
  - consumer: mcp-client\
    group: mcp' "$KONG_YML"
    else
        sed -i '/group: admin/a\  - consumer: mcp-client\n    group: mcp' "$KONG_YML"
    fi
    echo -e "${GREEN}✓ Added mcp-client ACL${NC}"
fi

# Step 5: Restart Kong
echo -e "${BLUE}[5/5] Restarting Kong...${NC}"
cd "$SUPABASE_DIR"
docker compose up -d kong
echo -e "${GREEN}✓ Kong restarted${NC}"

echo ""
echo -e "${BLUE}Waiting for Kong to be ready...${NC}"
sleep 5

# Verification
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           Verification                   ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# Test without key
HTTP_NO_KEY=$(curl -s -o /dev/null -w "%{http_code}" http://192.168.50.9:9080/mcp 2>/dev/null || echo "000")
# Test with key
HTTP_WITH_KEY=$(curl -s -o /dev/null -w "%{http_code}" -H "x-api-key: $MCP_API_KEY" http://192.168.50.9:9080/mcp 2>/dev/null || echo "000")

if [ "$HTTP_NO_KEY" = "401" ]; then
    echo -e "${GREEN}✓ Without API Key: 401 (Unauthorized)${NC}"
else
    echo -e "${YELLOW}⚠ Without API Key: $HTTP_NO_KEY (expected 401)${NC}"
fi

if [ "$HTTP_WITH_KEY" = "405" ] || [ "$HTTP_WITH_KEY" = "200" ]; then
    echo -e "${GREEN}✓ With API Key: $HTTP_WITH_KEY (Authenticated)${NC}"
else
    echo -e "${YELLOW}⚠ With API Key: $HTTP_WITH_KEY (expected 405 or 200)${NC}"
fi

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           .mcp.json Configuration        ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""
echo 'Add this to your project .mcp.json:'
echo ""
echo '{'
echo '  "mcpServers": {'
echo '    "supabase": {'
echo '      "type": "http",'
echo '      "url": "http://192.168.50.9:9080/mcp",'
echo '      "headers": {'
echo "        \"x-api-key\": \"$MCP_API_KEY\""
echo '      }'
echo '    }'
echo '  }'
echo '}'
echo ""
echo -e "${GREEN}Setup complete!${NC}"
