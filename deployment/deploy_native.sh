#!/usr/bin/env bash
# ==============================================================================
# Omnichannel Branch Assistant - Native Systemd Deployment Script
# Target: Ubuntu 22.04 / 24.04 LTS (No Docker, runs directly on host)
# ==============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}===================================================================${NC}"
echo -e "${BLUE}  Omnichannel Assistant - Native Systemd Server Installation       ${NC}"
echo -e "${BLUE}===================================================================${NC}"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}[ERROR] Lütfen bu scripti 'sudo' veya 'root' yetkisiyle çalıştırın.${NC}"
  exit 1
fi

INSTALL_DIR="/opt/omnichannel-branch-assistant"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo -e "${GREEN}[1/6] Sistem Paketleri & Bağımlılıklar Kuruluyor...${NC}"
apt-get update -y
apt-get install -y python3 python3-pip python3-venv nodejs npm redis-server \
                   chromium-browser fontconfig fonts-liberation ufw curl git

# 2. Setup UFW Firewall
echo -e "${GREEN}[2/6] UFW Güvenlik Duvarı Yapılandırılıyor...${NC}"
ufw --force enable
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw allow 80/tcp comment "HTTP"
ufw allow 443/tcp comment "HTTPS"
ufw allow 8000/tcp comment "Omnichannel Backend"

# 3. Copy Application to /opt
echo -e "${GREEN}[3/6] Kodlar ${INSTALL_DIR} Dizinine Hazırlanıyor...${NC}"
mkdir -p "${INSTALL_DIR}"
cp -r "${SOURCE_DIR}"/* "${INSTALL_DIR}/"

cd "${INSTALL_DIR}"

# 4. Setup Python Virtual Environment
echo -e "${GREEN}[4/6] Python 3 Virtual Environment ve Paketler Kuruluyor...${NC}"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt

# 5. Setup Node.js Bridge Dependencies
echo -e "${GREEN}[5/6] WhatsApp Bridge (Node.js) Paketleri Kuruluyor...${NC}"
cd "${INSTALL_DIR}/bridge"
npm install --production
cd "${INSTALL_DIR}"

# 6. Install and Start Systemd Services
echo -e "${GREEN}[6/6] Systemd Servisleri Tanımlanıyor ve Başlatılıyor...${NC}"
if [ ! -f .env ]; then
    cp .env.production.example .env
fi

cp deployment/omni-backend.service /etc/systemd/system/
cp deployment/omni-bridge.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable redis-server
systemctl restart redis-server

systemctl enable omni-backend
systemctl restart omni-backend

systemctl enable omni-bridge
systemctl restart omni-bridge

SERVER_IP=$(curl -s -4 ifconfig.me || echo "SERVER_IP")

echo -e "${BLUE}===================================================================${NC}"
echo -e "${GREEN}   Native Kurulum Tamamlandı! Servisler Arka Planda Çalışıyor!   ${NC}"
echo -e "${BLUE}===================================================================${NC}"
echo -e "Backend Status: ${YELLOW}systemctl status omni-backend${NC}"
echo -e "Bridge Status:  ${YELLOW}systemctl status omni-bridge${NC}"
echo -e "Canlı Loglar:   ${YELLOW}journalctl -u omni-backend -f${NC}"
echo -e "Dashboard:      ${YELLOW}http://${SERVER_IP}:8000${NC}"
echo -e "${BLUE}===================================================================${NC}"
