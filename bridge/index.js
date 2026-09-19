import pkg from "whatsapp-web.js";
const { Client, LocalAuth } = pkg;
import qrcodeTerminal from "qrcode-terminal";
import QRCode from "qrcode";
import axios from "axios";
import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

// Load environment configuration
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(__dirname, "../.env") });
dotenv.config();

const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";
const BRIDGE_API_PORT = parseInt(process.env.BRIDGE_API_PORT, 10) || 3001;
const AUTH_DATA_PATH = process.env.AUTH_DATA_PATH || path.resolve(__dirname, "./.wwebjs_auth");
const DEFAULT_BRANCH = process.env.DEFAULT_BRANCH || "istanbul-airport";
const BACKEND_WEBHOOK_URL = process.env.BACKEND_WEBHOOK_URL || `${BACKEND_URL}/webhook/whatsapp/${DEFAULT_BRANCH}`;

/**
 * Automatically ensures session folders, bot_audit.log, and .env files are in .gitignore
 */
function ensureGitignorePatterns() {
  try {
    const candidatePaths = [
      path.resolve(__dirname, "..", ".gitignore"),
      path.resolve(__dirname, ".gitignore"),
      path.resolve(process.cwd(), ".gitignore"),
    ];
    const requiredPatterns = [
      ".env",
      "*.env",
      "bot_audit.log",
      "*/bot_audit.log",
      ".wwebjs_auth/",
      "auth_info/",
      "session/",
      "*/.wwebjs_auth/",
      "*/auth_info/",
      "*/session/",
    ];
    for (const gPath of candidatePaths) {
      if (fs.existsSync(gPath)) {
        let content = fs.readFileSync(gPath, "utf-8");
        let modified = false;
        for (const pattern of requiredPatterns) {
          if (!content.includes(pattern)) {
            content += `\n${pattern}`;
            modified = true;
          }
        }
        if (modified) {
          fs.writeFileSync(gPath, content, "utf-8");
        }
      }
    }
  } catch (err) {}
}
ensureGitignorePatterns();

/**
 * Ensures POSIX directory permissions are strictly 0700 (owner only)
 * for session and authentication materials.
 */
function secureAuthDirectoryPermissions(dirPath) {
  try {
    if (!fs.existsSync(dirPath)) {
      fs.mkdirSync(dirPath, { recursive: true, mode: 0o700 });
    }
    if (process.platform !== "win32") {
      fs.chmodSync(dirPath, 0o700);
      try {
        const items = fs.readdirSync(dirPath, { withFileTypes: true });
        for (const item of items) {
          const itemPath = path.join(dirPath, item.name);
          if (item.isDirectory()) {
            secureAuthDirectoryPermissions(itemPath);
          } else {
            fs.chmodSync(itemPath, 0o600);
          }
        }
      } catch (childErr) {}
    }
  } catch (err) {
    console.warn("[SEC_AUTH_WARN] Could not set 0700 permissions on auth dir:", err.message);
  }
}
secureAuthDirectoryPermissions(AUTH_DATA_PATH);

let TRIGGER_PREFIX = process.env.TRIGGER_PREFIX !== undefined ? process.env.TRIGGER_PREFIX : "";

// Global Filter & Behavior Configuration
let FILTER_CONFIG = {
  only_unknown_contacts: process.env.ONLY_UNKNOWN_CONTACTS === "true",
  blacklist: [],
  debounce_seconds: parseInt(process.env.DEBOUNCE_SECONDS, 10) || 15,
  trigger_prefix: TRIGGER_PREFIX,
};

let client = null;
let bridgeState = "DISCONNECTED"; // DISCONNECTED | INITIALIZING | QR_READY | PAIRING_CODE_READY | AUTHENTICATED | CONNECTED
let currentQR = null;
let currentQRDataURL = null;
let currentPairingCode = null;
let currentPairingPhone = null;
let connectedUser = null;
let domSupervisorInterval = null;
let isPairingInProgress = false;
const recentLogs = [];
// JID Registry: maps phone digits / raw IDs to their exact WhatsApp JIDs (@c.us or @lid)
const jidRegistry = new Map();

function addLog(type, title, detail) {
  const logItem = {
    timestamp: new Date().toLocaleTimeString("tr-TR"),
    type,
    title,
    detail,
  };
  recentLogs.push(logItem);
  if (recentLogs.length > 50) {
    recentLogs.shift();
  }
}

function cleanChromiumLocks() {
  try {
    const sessionDir = path.join(AUTH_DATA_PATH, "session");
    if (fs.existsSync(sessionDir)) {
      const lockFiles = ["SingletonLock", "SingletonSocket", "SingletonCookie"];
      for (const lock of lockFiles) {
        const p = path.join(sessionDir, lock);
        if (fs.existsSync(p)) {
          try { fs.unlinkSync(p); } catch (e) {}
        }
      }
    }
  } catch (err) {}
}

// =========================================================
// WhatsApp Client Initializer
// =========================================================
async function initializeWhatsAppClient(force = false, pairPhoneNumber = null) {
  if (!force && bridgeState === "INITIALIZING" && client) {
    console.log("[BRIDGE] Client is already initializing...");
    return;
  }

  if (client) {
    try {
      if (client.pupBrowser) {
        try { await client.pupBrowser.close(); } catch (e) {}
      }
      await client.destroy();
    } catch (e) {
      console.warn("[BRIDGE WARN] Error destroying previous client:", e.message);
    }
    client = null;
  }
  cleanChromiumLocks();

  bridgeState = "INITIALIZING";
  currentQR = null;
  currentQRDataURL = null;
  currentPairingCode = null;
  if (pairPhoneNumber) {
    currentPairingPhone = String(pairPhoneNumber).replace(/\D/g, "");
  }
  connectedUser = null;

  if (currentPairingPhone) {
    addLog("info", "Telefon Eşleştirmesi Başlatılıyor", `${currentPairingPhone} için 8 haneli kod isteniyor...`);
  } else {
    addLog("info", "WhatsApp İstemcisi Başlatılıyor", "Puppeteer ve LocalAuth yükleniyor...");
  }

  client = new Client({
    authStrategy: new LocalAuth({ dataPath: AUTH_DATA_PATH }),
    puppeteer: {
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--no-zygote",
        "--disable-extensions",
      ],
    },
  });

  client.on("qr", async (qr) => {
    bridgeState = "QR_READY";
    currentQR = qr;
    qrcodeTerminal.generate(qr, { small: true });
    try {
      currentQRDataURL = await QRCode.toDataURL(qr, { margin: 2, scale: 8, errorCorrectionLevel: "M" });
    } catch (err) {}
    console.log("[BRIDGE] New QR code generated. Scan with WhatsApp on your phone.");
    addLog("warn", "QR Kod Hazır", "WhatsApp uygulamanızdan Bağlı Cihazlar ile taratınız.");
    ensureDOMSupervisorStarted();
  });

  client.on("authenticated", () => {
    bridgeState = "AUTHENTICATED";
    currentQR = null;
    currentQRDataURL = null;
    currentPairingCode = null;
    console.log("[BRIDGE] Session authenticated successfully.");
    addLog("info", "Oturum Doğrulandı", "Kullanıcı verileri senkronize ediliyor...");
    ensureDOMSupervisorStarted();
  });

  client.on("auth_failure", (msg) => {
    bridgeState = "DISCONNECTED";
    console.error("[BRIDGE] Authentication failure:", msg);
    addLog("error", "Doğrulama Başarısız", msg);
  });

  client.on("ready", () => {
    bridgeState = "CONNECTED";
    currentQR = null;
    currentQRDataURL = null;
    currentPairingCode = null;

    try {
      const info = client.info;
      connectedUser = {
        name: info?.pushname || "WhatsApp Yetkilisi",
        phone: info?.wid?.user || "Aktif",
        platform: info?.platform || "web",
      };
    } catch (e) {
      connectedUser = { name: "WhatsApp Yetkilisi", phone: "Aktif" };
    }

    console.log(`[BRIDGE] WhatsApp Connected successfully as ${connectedUser?.phone}!`);
    addLog("success", "WhatsApp Bağlantısı Aktif", `WhatsApp oturumu bağlandı: ${connectedUser?.phone}`);
    ensureDOMSupervisorStarted();
    populateJidRegistry();
  });

  async function populateJidRegistry() {
    try {
      if (!client) return;
      const chats = await client.getChats();
      for (const c of chats) {
        if (c.id && c.id._serialized) {
          const jid = c.id._serialized;
          const digits = jid.replace(/[^0-9]/g, "");
          if (digits) {
            jidRegistry.set(digits, jid);
          }
          jidRegistry.set(jid, jid);
        }
      }
      console.log(`[WHATSAPP BRIDGE] Registered ${jidRegistry.size} active chat JIDs into registry.`);
    } catch (e) {
      console.warn("[WHATSAPP BRIDGE] Could not pre-populate JID registry:", e.message);
    }
  }

  async function handleIncomingMessage(msg) {
    // 1. Resolve chat identifier
    const chatId = msg.id?.remote || (msg.fromMe ? msg.to : msg.from) || msg.from || "";

    // 2. Ignore status broadcasts or non-chat messages
    if (!chatId || chatId === "status@broadcast" || chatId.includes("@broadcast")) return;

    // 3. Ignore group messages (@g.us)
    if (chatId.includes("@g.us") || msg.isGroupMsg) return;

    // 4. Ignore non-text media for now
    if (msg.hasMedia || msg.type !== "chat") return;

    // Save JID mapping so outbound replies know the exact JID (@c.us or @lid)
    const cleanDigits = chatId.replace(/[^0-9]/g, "");
    if (cleanDigits) {
      jidRegistry.set(cleanDigits, chatId);
      jidRegistry.set(chatId, chatId);
    }

    const senderPhone = cleanDigits || chatId.replace("@c.us", "");
    const rawBody = (msg.body || "").trim();
    if (!rawBody) return;

    // 5. Blacklist Check
    if (FILTER_CONFIG.blacklist && FILTER_CONFIG.blacklist.length > 0) {
      const isBlocked = FILTER_CONFIG.blacklist.some((b) => b && senderPhone.includes(b.replace(/[^0-9]/g, "")));
      if (isBlocked) {
        console.log(`[BLACKLIST] Ignored blacklisted contact: ${chatId}`);
        return;
      }
    }

    // 6. Only Unknown Contacts Check (Ignore friends / personal contacts in phonebook)
    if (FILTER_CONFIG.only_unknown_contacts && !msg.fromMe) {
      try {
        const contact = await msg.getContact();
        if (contact && contact.isMyContact) {
          console.log(`[CONTACT_FILTER] Ignored saved personal contact in phonebook: ${contact.name || contact.pushname || chatId}`);
          return;
        }
      } catch (e) {
        console.warn("[WARN] Could not inspect contact book:", e.message);
      }
    }

    // 7. Check Trigger Prefix if configured (e.g. -test)
    let processedMessage = rawBody;
    if (TRIGGER_PREFIX) {
      const lowerRaw = rawBody.toLowerCase();
      const lowerPrefix = TRIGGER_PREFIX.toLowerCase();
      if (lowerRaw.startsWith(lowerPrefix)) {
        processedMessage = rawBody.slice(TRIGGER_PREFIX.length).trim();
        if (!processedMessage) return;
      } else if (lowerRaw === "/reset") {
        processedMessage = "/reset";
      } else {
        // Message does not start with trigger prefix -> Ignore
        return;
      }
    }

    let contactName = "";
    try {
      const contact = await msg.getContact();
      contactName = contact.name || contact.pushname || senderPhone;
    } catch (e) {}

    console.log(`[BRIDGE] Inbound message from ${chatId} (${contactName}): "${processedMessage.substring(0, 50)}..."`);
    addLog("message", `Gelen Mesaj: ${contactName || senderPhone}`, processedMessage.substring(0, 80));

    // Forward to FastAPI omnichannel backend (sending both phone and full chat_id)
    try {
      await axios.post(BACKEND_WEBHOOK_URL, {
        phone: senderPhone,
        chat_id: chatId,
        message: processedMessage,
        contact_name: contactName,
        branch_id: DEFAULT_BRANCH,
      }, { timeout: 10000 });
    } catch (err) {
      console.warn("[BRIDGE] Backend webhook delivery error:", err.message);
    }
  }

  // Handle incoming messages from external contacts
  client.on("message", handleIncomingMessage);

  // Handle self-messages or outgoing test triggers
  client.on("message_create", async (msg) => {
    const rawBody = (msg.body || "").trim();
    const chatId = msg.id?.remote || msg.to || msg.from || "";
    if (!chatId || chatId.includes("@broadcast") || chatId.includes("@g.us")) return;

    if (msg.fromMe && TRIGGER_PREFIX) {
      if (rawBody.toLowerCase().startsWith(TRIGGER_PREFIX.toLowerCase()) || rawBody.toLowerCase() === "/reset") {
        await handleIncomingMessage(msg);
      }
    }
  });

  client.on("disconnected", (reason) => {
    bridgeState = "DISCONNECTED";
    connectedUser = null;
    currentQR = null;
    currentQRDataURL = null;
    currentPairingCode = null;
    console.log("[BRIDGE] WhatsApp disconnected:", reason);
    addLog("warn", "Bağlantı Kesildi", String(reason));
  });

  try {
    await client.initialize();
  } catch (err) {
    bridgeState = "DISCONNECTED";
    console.error("[BRIDGE] Initialization error:", err.message);
    addLog("error", "Başlatma Hatası", err.message);
  }
}

// =========================================================
// DOM Supervisor for Real-time Web State Extraction
// =========================================================
function startDOMSupervisor() {
  if (domSupervisorInterval) clearInterval(domSupervisorInterval);

  domSupervisorInterval = setInterval(async () => {
    if (!client || !client.pupPage) return;

    try {
      const page = client.pupPage;
      const domStatus = await page.evaluate(async () => {
        // 1. Check if logged in (main chat pane, chat list, or cells presence)
        const mainPane = document.querySelector("#pane-side, [data-testid=chat-list], [aria-label=Chat\\ list], [aria-label=\"Sohbet listesi\"], [data-testid=cell-frame-container], [data-testid=chat-list-search]");
        if (mainPane) {
          return { state: "CONNECTED" };
        }

        // 2. Check for Pairing Code display
        const codeElement = document.querySelector("[data-testid=link-device-code]");
        if (codeElement && codeElement.innerText && codeElement.innerText.trim().length >= 8) {
          const raw = codeElement.innerText.trim();
          return {
            state: "PAIRING_CODE_READY",
            code: raw.includes("-") ? raw : `${raw.slice(0, 4)}-${raw.slice(4)}`,
          };
        }

        // Auto-dismiss feature modals/dialogs (e.g. "WhatsApp Web'deki yenilikler")
        const promoBtn = Array.from(document.querySelectorAll('div[role="dialog"] button, [data-testid="popup-controls-ok"]')).find((b) => {
          const t = b.innerText ? b.innerText.trim().toLowerCase() : "";
          return t.includes("devam") || t.includes("tamam") || t.includes("ok") || t.includes("continue") || t.includes("anladım");
        });
        if (promoBtn) {
          promoBtn.click();
        }
        const closeX = document.querySelector('div[role="dialog"] [data-icon="x"], div[role="dialog"] [data-testid="x-alt"]');
        if (closeX) {
          closeX.click();
        }

        // 3. Check for expired QR reload button
        const reloadBtn = Array.from(document.querySelectorAll("button, div[role=button], span")).find((b) => {
          const t = b.innerText ? b.innerText.trim().toLowerCase() : "";
          return t.includes("reload") || t.includes("yenile") || t.includes("select to reload");
        });
        if (reloadBtn) {
          reloadBtn.click();
          await new Promise((r) => setTimeout(r, 600));
        }

        // 4. Check for live QR data-ref on the container
        const qrContainer = document.querySelector("[data-ref]") || document.querySelector("[data-testid=link-device-qr-code]");
        const rawRef = qrContainer ? qrContainer.getAttribute("data-ref") : null;
        if (rawRef) {
          return {
            state: "QR_READY",
            rawRef: rawRef,
          };
        }

        // 5. Canvas fallback
        const canvas = document.querySelector("canvas");
        if (canvas && canvas.width > 50 && canvas.height > 50) {
          return {
            state: "QR_READY",
            canvasDataURL: canvas.toDataURL("image/png"),
          };
        }

        return { state: "WAITING" };
      });

      if (!domStatus) return;

      if (domStatus.state === "CONNECTED") {
        if (bridgeState !== "CONNECTED") {
          bridgeState = "CONNECTED";
          currentQR = null;
          currentQRDataURL = null;
          currentPairingCode = null;
          try {
            const info = client.info;
            connectedUser = {
              name: info?.pushname || "WhatsApp Yetkilisi",
              phone: info?.wid?.user || "Aktif",
              platform: info?.platform || "web",
            };
          } catch (e) {
            connectedUser = { name: "WhatsApp Yetkilisi", phone: "Aktif" };
          }
          addLog("success", "WhatsApp Bağlantısı Aktif", `WhatsApp oturumu bağlandı: ${connectedUser?.phone}`);
          populateJidRegistry();
        }
      } else if (domStatus.state === "PAIRING_CODE_READY") {
        if (domStatus.code && domStatus.code !== currentPairingCode) {
          currentPairingCode = domStatus.code;
          bridgeState = "PAIRING_CODE_READY";
        }
      } else if (domStatus.state === "QR_READY") {
        if (domStatus.rawRef && domStatus.rawRef !== currentQR) {
          currentQR = domStatus.rawRef;
          try {
            currentQRDataURL = await QRCode.toDataURL(domStatus.rawRef, {
              margin: 2,
              scale: 8,
              errorCorrectionLevel: "M",
            });
            bridgeState = "QR_READY";
          } catch (e) {}
        } else if (!currentQRDataURL && domStatus.canvasDataURL) {
          currentQRDataURL = domStatus.canvasDataURL;
          bridgeState = "QR_READY";
        }
      }
    } catch (err) {}
  }, 1500);
}

function ensureDOMSupervisorStarted() {
  if (!domSupervisorInterval) {
    startDOMSupervisor();
  }
}

// Mode switchers in WhatsApp Web UI
async function switchToQRMode(page) {
  if (!page) return null;
  return await page.evaluate(async () => {
    const qrBtn = Array.from(document.querySelectorAll('div[role="button"], button, span, div')).find((b) => {
      const t = b.innerText ? b.innerText.trim() : "";
      return t === "Log in with QR code" || t === "QR kodu ile bağla" || t.includes("Log in with QR");
    });
    if (qrBtn) {
      qrBtn.click();
      await new Promise((r) => setTimeout(r, 600));
    }
    const qrContainer = document.querySelector("[data-ref]") || document.querySelector("[data-testid=link-device-qr-code]");
    if (qrContainer && qrContainer.getAttribute("data-ref")) {
      return qrContainer.getAttribute("data-ref");
    }
    const canvas = document.querySelector("canvas");
    if (canvas && canvas.width > 50) {
      return canvas.toDataURL("image/png");
    }
    return null;
  });
}

async function switchToCodeMode(page) {
  if (!page) return false;
  return await page.evaluate(async () => {
    const phoneBtn = Array.from(document.querySelectorAll('div[role="button"], button, span, div')).find((b) => {
      const t = b.innerText ? b.innerText.trim() : "";
      return (
        t === "Log in with phone number" ||
        t === "Link with phone number instead." ||
        t === "Telefon numarasıyla bağla" ||
        t.includes("phone number instead")
      );
    });
    if (phoneBtn) {
      phoneBtn.click();
      await new Promise((r) => setTimeout(r, 600));
      return true;
    }
    return false;
  });
}

async function requestPairingCodeViaDOM(page, phoneNumber) {
  if (!page) throw new Error("Puppeteer sayfası hazır değil.");

  let formattedPhone = phoneNumber.replace(/[^0-9]/g, "");
  if (formattedPhone.startsWith("0")) {
    formattedPhone = "90" + formattedPhone.substring(1);
  } else if (!formattedPhone.startsWith("90") && formattedPhone.length === 10) {
    formattedPhone = "90" + formattedPhone;
  }

  const code = await page.evaluate(async (targetPhone) => {
    // Check if code is already displayed
    const existingCode = document.querySelector("[data-testid=link-device-code]");
    if (existingCode && existingCode.innerText && existingCode.innerText.trim().length >= 8) {
      const raw = existingCode.innerText.trim();
      return raw.includes("-") ? raw : `${raw.slice(0, 4)}-${raw.slice(4)}`;
    }

    // Click "Link with phone number instead."
    const linkPhoneBtn = Array.from(document.querySelectorAll("div[role=button], button, span, div")).find((b) => {
      const t = b.innerText ? b.innerText.trim() : "";
      return t === "Log in with phone number" || t === "Link with phone number instead." || t === "Telefon numarasıyla bağla";
    });
    if (linkPhoneBtn) {
      linkPhoneBtn.click();
      await new Promise((r) => setTimeout(r, 600));
    }

    // Find phone input
    let input = null;
    for (let i = 0; i < 30; i++) {
      input = document.querySelector("[data-testid=phone-number-input]") || document.querySelector("input[type=text]");
      if (input) break;
      await new Promise((r) => setTimeout(r, 200));
    }
    if (!input) throw new Error("Telefon numarası giriş alanı bulunamadı.");

    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    nativeSetter.call(input, targetPhone);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 500));

    // Click Next
    const nextBtn = Array.from(document.querySelectorAll("button, div[role=button]")).find((b) => {
      const t = b.innerText ? b.innerText.trim() : "";
      return t === "Next" || t === "İleri";
    });
    if (!nextBtn) throw new Error("İleri butonu bulunamadı.");
    nextBtn.click();

    // Wait for pairing code
    for (let i = 0; i < 35; i++) {
      await new Promise((r) => setTimeout(r, 400));

      const alert = Array.from(document.querySelectorAll("[role=alert], div[role=dialog]"))
        .map((e) => e.innerText)
        .find((t) => t && t.length > 5);
      if (alert && (alert.includes("Too many attempts") || alert.includes("çok fazla deneme"))) {
        throw new Error("WhatsApp güvenlik kısıtlaması: Bu numara için çok fazla deneme yapıldı.");
      }

      const activeCode = document.querySelector("[data-testid=link-device-code]");
      if (activeCode && activeCode.innerText && activeCode.innerText.trim().length >= 8) {
        const raw = activeCode.innerText.trim();
        return raw.includes("-") ? raw : `${raw.slice(0, 4)}-${raw.slice(4)}`;
      }

      // Fallback cell search
      const cells = Array.from(document.querySelectorAll("[data-testid=link-device-code-cell], div, span")).filter((el) => {
        return el.children.length === 0 && el.innerText && el.innerText.trim().length === 1 && /^[A-Z0-9]$/.test(el.innerText.trim());
      });
      const charList = cells.map((c) => c.innerText.trim());
      if (charList.length >= 8) {
        return `${charList.slice(0, 4).join("")}-${charList.slice(4, 8).join("")}`;
      }
    }

    throw new Error("Eşleştirme kodu zaman aşımına uğradı. Lütfen tekrar deneyiniz.");
  }, formattedPhone);

  return code;
}

// =========================================================
// Express Bridge Management Endpoints
// =========================================================
const app = express();
app.use(cors());
app.use(express.json());

// Status endpoint (supports both /status and /api/status)
function handleStatus(req, res) {
  res.json({
    state: bridgeState,
    status: bridgeState,
    connected: bridgeState === "CONNECTED",
    connectedUser,
    hasQR: Boolean(currentQRDataURL || currentQR),
    qr: currentQRDataURL,
    qrDataURL: currentQRDataURL,
    qrCode: currentQR,
    pairingCode: currentPairingCode,
    pairingPhone: currentPairingPhone,
    logs: recentLogs,
  });
}
app.get("/status", handleStatus);
app.get("/api/status", handleStatus);

// QR endpoint
app.get("/qr", (req, res) => {
  res.json({
    state: bridgeState,
    qrDataURL: currentQRDataURL,
    qrCode: currentQR,
  });
});
app.get("/api/qr", (req, res) => {
  res.json({
    state: bridgeState,
    qrDataURL: currentQRDataURL,
    qrCode: currentQR,
  });
});

// Reconnect / Connect
async function handleConnect(req, res) {
  console.log("[BRIDGE] Received reconnect / initialize request.");
  initializeWhatsAppClient(true);
  res.json({ success: true, status: "INITIALIZING", message: "WhatsApp istemcisi başlatılıyor..." });
}
app.post("/reconnect", handleConnect);
app.post("/api/connect", handleConnect);

// Switch Mode (QR vs Pairing Code)
app.post("/api/switch-mode", async (req, res) => {
  const { mode } = req.body || {};
  if (!client || !client.pupPage) {
    return res.json({ success: false, state: bridgeState, error: "Tarayıcı henüz hazır değil" });
  }

  try {
    if (mode === "code") {
      await switchToCodeMode(client.pupPage);
      bridgeState = "PAIRING_CODE_READY";
      res.json({ success: true, mode: "code" });
    } else {
      const raw = await switchToQRMode(client.pupPage);
      bridgeState = "QR_READY";
      if (raw) {
        currentQR = raw;
        currentQRDataURL = raw.startsWith("data:") ? raw : await QRCode.toDataURL(raw, { margin: 2, scale: 8, errorCorrectionLevel: "M" });
      }
      res.json({ success: true, mode: "qr", qr: currentQRDataURL });
    }
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Request Pairing Code
app.post("/api/pair", async (req, res) => {
  const { phoneNumber } = req.body || {};
  if (!phoneNumber || typeof phoneNumber !== "string") {
    return res.status(400).json({ success: false, error: "Lütfen geçerli bir telefon numarası girin." });
  }

  let clean = phoneNumber.replace(/\D/g, "");
  if (clean.startsWith("0")) {
    clean = "90" + clean.substring(1);
  } else if (!clean.startsWith("90") && clean.length === 10) {
    clean = "90" + clean;
  }

  if (isPairingInProgress) {
    return res.status(429).json({ success: false, error: "Eşleştirme kodu zaten alınıyor, lütfen bekleyin..." });
  }
  isPairingInProgress = true;
  currentPairingPhone = clean;

  try {
    if (!client || !client.pupPage) {
      console.log(`[PAIR] Initializing client for ${clean}...`);
      initializeWhatsAppClient(true, clean);
      for (let i = 0; i < 50; i++) {
        await new Promise((r) => setTimeout(r, 500));
        if (client && client.pupPage) break;
      }
      if (!client || !client.pupPage) {
        throw new Error("WhatsApp Web tarayıcısı hazırlanamadı.");
      }
    }

    console.log(`[PAIR] Requesting pairing code via DOM for ${clean}...`);
    const code = await requestPairingCodeViaDOM(client.pupPage, clean);
    if (code) {
      bridgeState = "PAIRING_CODE_READY";
      currentPairingCode = code;
      ensureDOMSupervisorStarted();
      addLog("warn", "Eşleştirme Kodu Hazır", `${clean} için 8 haneli kod: ${code}`);

      return res.json({
        success: true,
        code,
        pairingCode: code,
        phoneNumber: clean,
        message: `${clean} için eşleştirme kodu üretildi: ${code}`,
      });
    } else {
      throw new Error("Eşleştirme kodu oluşturulamadı.");
    }
  } catch (err) {
    console.error("[PAIR ERROR]:", err.message);
    addLog("error", "Eşleştirme Hatası", err.message);
    res.status(500).json({ success: false, error: err.message });
  } finally {
    isPairingInProgress = false;
  }
});

// Disconnect / Clear Session
app.post("/api/disconnect", async (req, res) => {
  const { clearSession } = req.body || {};
  try {
    if (client) {
      try { await client.logout(); } catch (e) {}
      try { await client.destroy(); } catch (e) {}
      client = null;
    }
    if (clearSession) {
      try {
        if (fs.existsSync(AUTH_DATA_PATH)) {
          fs.rmSync(AUTH_DATA_PATH, { recursive: true, force: true });
        }
      } catch (rmErr) {}
    }
    bridgeState = "DISCONNECTED";
    connectedUser = null;
    currentQR = null;
    currentQRDataURL = null;
    currentPairingCode = null;
    addLog("info", "Oturum Kapatıldı", clearSession ? "Oturum ve önbellek temizlendi." : "Bağlantı kesildi.");
    res.json({ success: true, status: "DISCONNECTED", clearSession });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Screenshot Debug Endpoint
app.get("/api/debug/screenshot", async (req, res) => {
  if (!client || !client.pupPage) {
    return res.status(404).json({ error: "Puppeteer sayfası henüz hazır değil" });
  }
  try {
    const buffer = await client.pupPage.screenshot({ type: "png", fullPage: true });
    res.setHeader("Content-Type", "image/png");
    res.send(buffer);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Outbound Send Message Endpoint
app.post(["/send-message", "/api/send"], async (req, res) => {
  const { chatId, message } = req.body || {};
  if (!chatId || !message) {
    return res.status(400).json({ error: "chatId ve message zorunludur" });
  }

  if (!client || (bridgeState !== "CONNECTED" && bridgeState !== "AUTHENTICATED")) {
    return res.status(503).json({ error: "WhatsApp istemcisi bağlı değil" });
  }

  const clean = String(chatId).replace(/[^0-9]/g, "");
  let targetChat = chatId;
  if (jidRegistry.has(clean)) {
    targetChat = jidRegistry.get(clean);
  } else if (jidRegistry.has(chatId)) {
    targetChat = jidRegistry.get(chatId);
  } else if (!targetChat.includes("@")) {
    // Standard phone numbers with country codes (like 905..., 49...) are @c.us
    // Internal WhatsApp Linked IDs (LIDs) are typically 15+ digits (e.g. 229055504875721)
    if (clean.length >= 14 && !clean.startsWith("90") && !clean.startsWith("49") && !clean.startsWith("1")) {
      targetChat = `${clean}@lid`;
    } else {
      targetChat = `${clean}@c.us`;
    }
  }

  console.log(`[WHATSAPP BRIDGE] Dispatching outbound message to ${targetChat} (requested: ${chatId})`);

  try {
    await client.sendMessage(targetChat, message);
    console.log(`[WHATSAPP BRIDGE] Successfully delivered message to ${targetChat}`);
    return res.json({ status: "sent", targetChat });
  } catch (err) {
    console.warn(`[WHATSAPP BRIDGE] Send to ${targetChat} failed: ${err.message}`);

    // If @c.us failed with LID error, retry with @lid immediately
    if (targetChat.endsWith("@c.us") && (err.message.includes("LID") || err.message.includes("No LID"))) {
      const lidChat = targetChat.replace("@c.us", "@lid");
      try {
        console.log(`[WHATSAPP BRIDGE] Retrying with LID target: ${lidChat}`);
        await client.sendMessage(lidChat, message);
        jidRegistry.set(clean, lidChat);
        console.log(`[WHATSAPP BRIDGE] Successfully delivered via LID fallback: ${lidChat}`);
        return res.json({ status: "sent", targetChat: lidChat, fallback: "lid" });
      } catch (lidErr) {
        console.error(`[WHATSAPP BRIDGE] LID fallback error: ${lidErr.message}`);
      }
    }

    // If @lid failed, try @c.us
    if (targetChat.endsWith("@lid")) {
      const cusChat = targetChat.replace("@lid", "@c.us");
      try {
        console.log(`[WHATSAPP BRIDGE] Retrying with @c.us target: ${cusChat}`);
        await client.sendMessage(cusChat, message);
        jidRegistry.set(clean, cusChat);
        console.log(`[WHATSAPP BRIDGE] Successfully delivered via @c.us fallback: ${cusChat}`);
        return res.json({ status: "sent", targetChat: cusChat, fallback: "c.us" });
      } catch (cusErr) {
        console.error(`[WHATSAPP BRIDGE] @c.us fallback error: ${cusErr.message}`);
      }
    }

    addLog("error", "Mesaj Gönderim Hatası", `${targetChat}: ${err.message}`);
    res.status(500).json({ error: err.message, targetChat });
  }
});

// API: Update Trigger Prefix
app.post("/api/prefix", (req, res) => {
  const { prefix } = req.body || {};
  TRIGGER_PREFIX = typeof prefix === "string" ? prefix.trim() : "";
  FILTER_CONFIG.trigger_prefix = TRIGGER_PREFIX;
  addLog("info", "Önek Güncellendi", TRIGGER_PREFIX ? `Yeni test öneki: "${TRIGGER_PREFIX}"` : "Önek kaldırıldı (Tüm mesajlar)");
  res.json({ success: true, triggerPrefix: TRIGGER_PREFIX });
});

// API: Update Full Filter & Debounce Configuration
app.post("/api/config", (req, res) => {
  const body = req.body || {};
  if (typeof body.only_unknown_contacts === "boolean") FILTER_CONFIG.only_unknown_contacts = body.only_unknown_contacts;
  if (Array.isArray(body.blacklist)) FILTER_CONFIG.blacklist = body.blacklist;
  if (typeof body.debounce_seconds === "number") FILTER_CONFIG.debounce_seconds = Math.max(1, body.debounce_seconds);
  if (typeof body.trigger_prefix === "string") {
    FILTER_CONFIG.trigger_prefix = body.trigger_prefix.trim();
    TRIGGER_PREFIX = FILTER_CONFIG.trigger_prefix;
  }
  console.log("[CONFIG] Updated bridge filter settings:", FILTER_CONFIG);
  addLog("info", "Filtre Ayarları Güncellendi", `Rehber Filtresi: ${FILTER_CONFIG.only_unknown_contacts ? 'Açık' : 'Kapalı'}, Önek: "${TRIGGER_PREFIX}"`);
  res.json({ success: true, config: FILTER_CONFIG });
});

app.get("/api/config", (req, res) => {
  res.json({ success: true, config: FILTER_CONFIG });
});

// API: Event Logs
app.get("/api/logs", (req, res) => {
  res.json({ logs: recentLogs });
});

// Sync persistent filter settings from FastAPI on boot
async function syncFilterSettingsFromBackend() {
  try {
    const backendBase = BACKEND_URL;
    const res = await axios.get(`${backendBase}/api/settings/filters`, { timeout: 4000 });
    if (res.data) {
      if (typeof res.data.only_unknown_contacts === "boolean") FILTER_CONFIG.only_unknown_contacts = res.data.only_unknown_contacts;
      if (typeof res.data.trigger_prefix === "string") {
        FILTER_CONFIG.trigger_prefix = res.data.trigger_prefix.trim();
        TRIGGER_PREFIX = FILTER_CONFIG.trigger_prefix;
      }
      if (typeof res.data.debounce_seconds === "number") FILTER_CONFIG.debounce_seconds = res.data.debounce_seconds;
      if (Array.isArray(res.data.blacklist)) FILTER_CONFIG.blacklist = res.data.blacklist;
      console.log("[BRIDGE] Synced filter configuration from FastAPI backend:", FILTER_CONFIG);
    }
  } catch (err) {
    console.log("[BRIDGE] Could not sync filter settings from backend on boot (will use defaults):", err.message);
  }
}

// Start listening
app.listen(BRIDGE_API_PORT, () => {
  console.log(`[BRIDGE] WhatsApp Web Session Bridge listening on http://127.0.0.1:${BRIDGE_API_PORT}`);
  console.log(`[BRIDGE] Default Branch: ${DEFAULT_BRANCH} -> Webhook: ${BACKEND_WEBHOOK_URL}`);
  syncFilterSettingsFromBackend();
  // Auto-initialize if session directory exists or AUTO_START_BRIDGE is true
  const sessionExists = fs.existsSync(AUTH_DATA_PATH) && fs.existsSync(path.join(AUTH_DATA_PATH, "session"));
  if (sessionExists || process.env.AUTO_START_BRIDGE === "true") {
    console.log("[BRIDGE] Existing WhatsApp session found, auto-initializing client...");
    initializeWhatsAppClient();
  } else {
    console.log("[BRIDGE] No existing session found. Awaiting pairing or manual connect.");
  }
});

