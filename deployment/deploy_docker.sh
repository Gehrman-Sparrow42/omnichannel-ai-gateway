#!/usr/bin/env bash
# ==============================================================================
# Omnichannel Branch Assistant - Production Docker Deployment Script
# Target OS: Ubuntu 20.04 / 22.04 / 24.04 LTS, Debian 11 / 12
# ==============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}===================================================================${NC}"
echo -e "${BLUE}   Omnichannel Branch Assistant - Production Server Deployment     ${NC}"
echo -e "${BLUE}===================================================================${NC}"

# 1. Check Root Privileges
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}[ERROR] Lütfen bu scripti 'sudo' veya 'root' yetkisiyle çalıştırın.${NC}"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo -e "${GREEN}[1/6] Çekirdek Sistem Paketleri Güncelleniyor...${NC}"
apt-get update -y
apt-get install -y curl wget git ufw htop ca-certificates gnupg lsb-release

# 2. Check / Install Docker
echo -e "${GREEN}[2/6] Docker & Docker Compose Kontrol Ediliyor...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Docker bulunamadı. Resmi Docker deposundan kuruluyor...${NC}"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}[OK] Docker başarıyla kuruldu.${NC}"
else
    echo -e "${GREEN}[OK] Docker zaten kurulu: $(docker --version)${NC}"
fi

# 3. Setup Firewall (UFW)
echo -e "${GREEN}[3/6] Güvenlik Duvarı (UFW) Yapılandırılıyor...${NC}"
ufw --force enable
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH Remote Access"
ufw allow 80/tcp comment "HTTP Web Access / Certbot"
ufw allow 443/tcp comment "HTTPS SSL Encrypted"
ufw allow 8000/tcp comment "Omnichannel Backend & Dashboard"
# 3001 (Bridge) ve 6379 (Redis) UFW ile DIŞ DÜNYAYA KAPATILIR! (Sadece 127.0.0.1 Docker içinden erişilir)
ufw status verbose

# 4. Check & Prepare .env
echo -e "${GREEN}[4/6] Ortam Değişkenleri (.env) Kontrol Ediliyor...${NC}"
if [ ! -f .env ]; then
    if [ -f .env.production.example ]; then
        cp .env.production.example .env
        echo -e "${YELLOW}[UYARI] .env dosyası oluşturuldu (.env.production.example baz alındı).${NC}"
        echo -e "${YELLOW}Lütfen OpenAI API anahtarınızı tanımlamak için .env dosyasını düzenleyin: nano .env${NC}"
    else
        touch .env
    fi
else
    echo -e "${GREEN}[OK] Mevcut .env dosyası bulundu.${NC}"
fi

# 5. Build and Launch Containers
echo -e "${GREEN}[5/6] Docker Konteynerleri Derleniyor ve Başlatılıyor...${NC}"
docker compose down --remove-orphans || true
docker compose build --parallel
docker compose up -d

# 6. Verify Health
echo -e "${GREEN}[6/6] Servis Sağlığı Doğrulanıyor (Bekleniyor 10 sn)...${NC}"
sleep 10
docker compose ps

SERVER_IP=$(curl -s -4 ifconfig.me || echo "SERVER_IP")

echo -e "${BLUE}===================================================================${NC}"
echo -e "${GREEN}   Tebrikler! Omnichannel Assistant Başarıyla Canlıya Alındı!   ${NC}"
echo -e "${BLUE}===================================================================${NC}"
echo -e "Yönetim Paneli & API: ${YELLOW}http://${SERVER_IP}:8000${NC}"
echo -e "Sağlık Kontrolü (Health): ${YELLOW}http://${SERVER_IP}:8000/health${NC}"
echo -e "Şube Durumları:       ${YELLOW}http://${SERVER_IP}:8000/api/branches${NC}"
echo -e "WhatsApp Durumu:      ${YELLOW}http://${SERVER_IP}:8000/api/channels/whatsapp/status${NC}"
echo ""
echo -e "WhatsApp QR Kodunu Okutmak İçin:"
echo -e "  1. Tarayıcınızdan http://${SERVER_IP}:8000 adresini açın"
echo -e "  2. Veya logları canlı izleyin: ${YELLOW}docker compose logs -f bridge${NC}"
echo ""
echo -e "Nginx ve Ücretsiz SSL (Certbot) Kurmak İçin:"
echo -e "  sudo apt install -y nginx certbot python3-certbot-nginx"
echo -e "  sudo cp deployment/nginx-omnichannel.conf /etc/nginx/sites-available/omnichannel.conf"
echo -e "  sudo ln -s /etc/nginx/sites-available/omnichannel.conf /etc/nginx/sites-enabled/"
echo -e "  sudo certbot --nginx -d your-domain.com"
echo -e "${BLUE}===================================================================${NC}"
