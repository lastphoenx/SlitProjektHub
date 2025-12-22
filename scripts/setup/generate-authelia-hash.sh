#!/bin/bash
# Generate Authelia password hashes interactively

echo "=========================================="
echo "Authelia Password Hash Generator"
echo "=========================================="
echo ""

if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed!"
    exit 1
fi

read -p "Enter username: " username
read -sp "Enter password: " password
echo ""
read -sp "Confirm password: " password2
echo ""

if [ "$password" != "$password2" ]; then
    echo "❌ Passwords do not match!"
    exit 1
fi

echo ""
echo "Generating hash for user: $username"
echo ""

# Generate hash
hash=$(docker run --rm authelia/authelia:latest authelia crypto hash generate argon2 --password "$password" 2>/dev/null | grep "Output:" | cut -d' ' -f2)

if [ -z "$hash" ]; then
    echo "❌ Failed to generate hash"
    exit 1
fi

echo "=========================================="
echo "✅ Hash generated successfully!"
echo "=========================================="
echo ""
echo "Username: $username"
echo "Hash:     $hash"
echo ""
echo "Add this to authelia/users_database.yml:"
echo ""
echo "  $username:"
echo "    displayname: \"$username\""
echo "    password: \"$hash\""
echo "    email: $username@mainedomain.com"
echo "    groups:"
echo "      - users"
echo ""
