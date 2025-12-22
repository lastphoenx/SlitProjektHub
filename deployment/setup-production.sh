#!/bin/bash
# Setup script for ProjektHub Production Deployment
# Run this ONCE before first docker-compose up

set -e

echo "========================================="
echo "ProjektHub Production Setup"
echo "========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# 1. Create .env.production file if it doesn't exist
if [ ! -f .env.production ]; then
    echo -e "${YELLOW}[1/4] Creating .env.production with secrets...${NC}"
    
    JWT_SECRET=$(openssl rand -base64 32)
    SESSION_SECRET=$(openssl rand -base64 32)
    
    cat > .env.production << EOF
# API Keys
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
MISTRAL_API_KEY=...

# Authelia Secrets (auto-generated)
JWT_SECRET=$JWT_SECRET
SESSION_SECRET=$SESSION_SECRET

# Email (Optional - for password reset notifications)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_SENDER=noreply@mainedomain.com

# Domain
DOMAIN=mainedomain.com
PROJEKTHUB_DOMAIN=projekthub.mainedomain.com
AUTH_DOMAIN=auth.mainedomain.com
EOF
    
    echo -e "${GREEN}✓ Created .env.production${NC}"
    echo -e "${YELLOW}⚠️  Edit .env.production and fill in your API keys!${NC}"
    echo ""
else
    echo -e "${GREEN}✓ .env.production already exists${NC}"
    echo ""
fi

# 2. Generate Authelia directory structure
echo -e "${YELLOW}[2/4] Setting up Authelia directories...${NC}"
mkdir -p authelia/db
touch authelia/db.sqlite3
chmod 600 authelia/db.sqlite3
echo -e "${GREEN}✓ Authelia directories created${NC}"
echo ""

# 3. Generate password hashes
echo -e "${YELLOW}[3/4] Generating Argon2id password hashes...${NC}"
echo ""
echo "Run these commands to generate password hashes (replace 'password' with your password):"
echo ""
echo -e "${YELLOW}docker run authelia/authelia:latest authelia crypto hash generate argon2 --password 'your-password'${NC}"
echo ""
echo "Then update authelia/users_database.yml with the generated hashes."
echo ""

# 4. Update configuration.yml with secrets
echo -e "${YELLOW}[4/4] Updating configuration.yml with secrets...${NC}"

# Load secrets from .env
JWT_SECRET=$(grep "^JWT_SECRET=" .env.production | cut -d '=' -f2)
SESSION_SECRET=$(grep "^SESSION_SECRET=" .env.production | cut -d '=' -f2)

# Update configuration.yml
sed -i "s|your-very-secure-random-jwt-secret-here-min-32-chars|$JWT_SECRET|g" authelia/configuration.yml
sed -i "s|another-very-secure-random-session-secret-min-32-chars|$SESSION_SECRET|g" authelia/configuration.yml

echo -e "${GREEN}✓ Secrets updated in configuration.yml${NC}"
echo ""

echo "========================================="
echo -e "${GREEN}Setup complete!${NC}"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Edit .env.production and fill in API keys"
echo "2. Run: docker run authelia/authelia:latest authelia crypto hash generate argon2"
echo "3. Update authelia/users_database.yml with password hashes"
echo "4. Run: docker-compose -f docker-compose.production.yml up -d"
echo ""
