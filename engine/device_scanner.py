"""
Device Scanner — passive nearby device name discovery via mDNS and WiFi probe requests.

Captures device display names (e.g. "Knibb High Football Rules") from:
  1. mDNS/Bonjour service announcements on the local network (best for device hostnames)
  2. 802.11 probe request frames on the monitor interface (works off-network)

These names are fed to DeviceCorrelator in csi_processor.py to build associations
between device identity and CSI-detected biometric subjects.
"""

import threading
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ─── mDNS / Bonjour ──────────────────────────────────────────────────────────

try:
    from zeroconf import Zeroconf, ServiceBrowser
    ZEROCONF_AVAILABLE = True
except ImportError:
    ZEROCONF_AVAILABLE = False
    logger.warning("[DeviceScanner] zeroconf not installed — mDNS scanning disabled. pip3 install zeroconf")

# Service types that carry device hostnames. Each announcement includes the
# device's .local hostname, which iOS/macOS sets to the device's custom name.
MDNS_SERVICE_TYPES = [
    '_apple-mobdev2._tcp.local.',    # iPhones/iPads (direct USB/WiFi link)
    '_companion-link._tcp.local.',   # Apple Watch companion
    '_sleep-proxy._udp.local.',      # Bonjour Sleep Proxy (macOS)
    '_googlecast._tcp.local.',       # Chromecast / Google devices
    '_spotify-connect._tcp.local.',  # Spotify Connect (iOS, Android, desktop)
    '_http._tcp.local.',             # Generic HTTP services (laptops, phones)
    '_workstation._tcp.local.',      # Linux/Windows (avahi-daemon)
    '_rdlink._tcp.local.',           # Apple Watch pairing link
    '_androidtvremote2._tcp.local.', # Android TV
]


class _MDNSListener:
    """Receives mDNS service events and writes device info to the shared registry."""

    def __init__(self, registry: dict, lock: threading.Lock):
        self._registry = registry
        self._lock = lock

    def add_service(self, zc, type_, name):
        try:
            info = zc.get_service_info(type_, name)
        except Exception:
            return
        if not info:
            return

        hostname = (info.server or '').rstrip('.')
        if not hostname:
            return

        # Hostname format: "Knibb-High-Football-Rules.local"
        # Convert to display name: "Knibb High Football Rules"
        display_name = (
            hostname
            .replace('.local', '')
            .replace('-', ' ')
            .replace('_', ' ')
        ).strip()

        addresses = []
        try:
            addresses = [str(a) for a in info.parsed_addresses()]
        except Exception:
            pass

        self._upsert(hostname.lower(), display_name, hostname, addresses, 'mdns')

    def remove_service(self, zc, type_, name):
        pass  # let cleanup thread handle staleness

    def update_service(self, zc, type_, name):
        self.add_service(zc, type_, name)

    def _upsert(self, key, display_name, hostname, addresses, source):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if key not in self._registry:
                self._registry[key] = {
                    'display_name': display_name,
                    'hostname': hostname,
                    'addresses': addresses,
                    'source': source,
                    'first_seen': now,
                    'last_seen': now,
                    'rssi': None,
                }
                logger.info(f"[DeviceScanner] mDNS: '{display_name}' ({hostname})")
            else:
                self._registry[key]['last_seen'] = now
                self._registry[key]['addresses'] = addresses


# ─── WiFi Probe Request Sniffer ───────────────────────────────────────────────

try:
    from scapy.all import sniff, Dot11, Dot11ProbeReq, Dot11Elt, RadioTap
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False
    logger.warning("[DeviceScanner] scapy not installed — probe request scanning disabled. pip3 install scapy")


def _handle_probe(pkt, registry: dict, lock: threading.Lock):
    """
    Extract device hint from an 802.11 probe request frame.

    The SSID in a probe request is a network the device previously joined.
    While not the device's own name, it can be a strong hint (e.g. a home
    network named after the owner). We record it under the source MAC.
    """
    if not (pkt.haslayer(Dot11) and pkt.haslayer(Dot11ProbeReq)):
        return

    mac = pkt[Dot11].addr2
    if not mac or mac == 'ff:ff:ff:ff:ff:ff':
        return

    # Many modern devices randomize their MAC per probe burst, so we key on
    # (mac, ssid) rather than mac alone to avoid collisions.
    ssid = ''
    elt = pkt.getlayer(Dot11Elt)
    while elt:
        if elt.ID == 0 and elt.info:
            try:
                ssid = elt.info.decode('utf-8', errors='replace').strip()
            except Exception:
                pass
            break
        elt = elt.payload.getlayer(Dot11Elt) if elt.payload else None

    if not ssid:
        return  # wildcard probe — no useful name info

    rssi = getattr(pkt, 'dBm_AntSignal', None)
    key = f"probe_{mac.replace(':', '')}_{ssid[:16]}"
    now = datetime.now(timezone.utc).isoformat()

    with lock:
        if key not in registry:
            registry[key] = {
                'display_name': ssid,
                'hostname': None,
                'mac': mac,
                'addresses': [],
                'source': 'probe_request',
                'probed_ssids': [ssid],
                'first_seen': now,
                'last_seen': now,
                'rssi': rssi,
            }
            logger.info(f"[DeviceScanner] Probe: {mac} → '{ssid}'")
        else:
            registry[key]['last_seen'] = now
            if rssi is not None:
                registry[key]['rssi'] = rssi


# ─── DeviceScanner ───────────────────────────────────────────────────────────

class DeviceScanner:
    """
    Passively collects nearby device display names via mDNS and probe requests.

    Usage:
        scanner = DeviceScanner(probe_iface='mon0')
        scanner.start()
        ...
        devices = scanner.get_visible()  # {key: {display_name, source, ...}}
        scanner.stop()

    The probe_iface should be the monitor-mode interface (mon0 or wlan0 in
    monitor mode). mDNS listens on all interfaces automatically via Zeroconf.
    """

    def __init__(self, probe_iface: Optional[str] = None, stale_seconds: int = 120):
        self._registry: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._probe_iface = probe_iface
        self._stale_seconds = stale_seconds
        self._running = False
        self._zc = None

    def start(self):
        self._running = True

        if ZEROCONF_AVAILABLE:
            threading.Thread(target=self._run_mdns, daemon=True, name='mdns-scanner').start()

        if SCAPY_AVAILABLE and self._probe_iface:
            threading.Thread(target=self._run_probe_sniffer, daemon=True, name='probe-sniffer').start()

        threading.Thread(target=self._run_cleanup, daemon=True, name='device-cleanup').start()

        logger.info(
            f"[DeviceScanner] Started "
            f"(mDNS={'yes' if ZEROCONF_AVAILABLE else 'no'}, "
            f"probes={'yes on ' + self._probe_iface if (SCAPY_AVAILABLE and self._probe_iface) else 'no'})"
        )

    def stop(self):
        self._running = False
        if self._zc:
            try:
                self._zc.close()
            except Exception:
                pass

    def get_visible(self) -> Dict[str, dict]:
        """Thread-safe snapshot of currently visible devices."""
        with self._lock:
            return dict(self._registry)

    # ── Background threads ────────────────────────────────────────────────────

    def _run_mdns(self):
        try:
            self._zc = Zeroconf()
            listener = _MDNSListener(self._registry, self._lock)
            browsers = [ServiceBrowser(self._zc, stype, listener) for stype in MDNS_SERVICE_TYPES]
            while self._running:
                time.sleep(1)
        except Exception as e:
            logger.error(f"[DeviceScanner] mDNS thread error: {e}")
        finally:
            if self._zc:
                try:
                    self._zc.close()
                except Exception:
                    pass

    def _run_probe_sniffer(self):
        while self._running:
            try:
                sniff(
                    iface=self._probe_iface,
                    prn=lambda pkt: _handle_probe(pkt, self._registry, self._lock),
                    filter='type mgt subtype probe-req',
                    store=False,
                    timeout=5,
                )
            except Exception as e:
                logger.error(f"[DeviceScanner] Probe sniffer error: {e}")
                time.sleep(5)

    def _run_cleanup(self):
        """Expire devices not seen within stale_seconds."""
        while self._running:
            time.sleep(30)
            cutoff = time.time() - self._stale_seconds
            with self._lock:
                stale = [
                    k for k, v in self._registry.items()
                    if _iso_to_timestamp(v['last_seen']) < cutoff
                ]
                for k in stale:
                    logger.info(f"[DeviceScanner] Expired: '{self._registry[k]['display_name']}'")
                    del self._registry[k]


def _iso_to_timestamp(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0
