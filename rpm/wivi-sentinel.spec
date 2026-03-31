Name:           wivi-sentinel
Version:        2.0.0
Release:        1%{?dist}
Summary:        WiFi CSI biometric detection system
License:        MIT
URL:            https://github.com/YOUR_USER/wivi-sentinel

Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

# Core runtime
Requires:       python3 >= 3.8
Requires:       python3-pip
Requires:       python3-virtualenv
Requires:       systemd

# WiFi setup (NetworkManager)
Requires:       NetworkManager
Requires:       NetworkManager-wifi

# SciPy / NumPy native deps
Requires:       libgfortran
Requires:       openblas-serial

# Scapy needs libpcap
Requires:       libpcap

# ESP32 serial — user needs dialout group
Requires:       coreutils

# mDNS device discovery
Requires:       avahi
Requires:       nss-mdns

# Build deps (only needed to create RPM)
BuildRequires:  python3-devel

%description
Wi-Vi Sentinel is a passive WiFi biometric detection system. It uses ESP32
Channel State Information (CSI) to detect and identify humans and animals
through walls by their unique heartbeat and gait signatures.

Includes an interactive setup wizard that configures WiFi, detects your ESP32
hardware, and starts the server automatically.

%prep
%setup -q

%install
# Application directory
install -d %{buildroot}/opt/%{name}
install -d %{buildroot}/opt/%{name}/engine
install -d %{buildroot}/opt/%{name}/dist
install -d %{buildroot}/opt/%{name}/dist/assets
install -d %{buildroot}/opt/%{name}/data

# Core application files
install -m 644 server.py           %{buildroot}/opt/%{name}/server.py
install -m 644 requirements.txt    %{buildroot}/opt/%{name}/requirements.txt
install -m 644 .env.example        %{buildroot}/opt/%{name}/.env.example
install -m 755 start.sh            %{buildroot}/opt/%{name}/start.sh

# Engine
install -m 644 engine/*.py         %{buildroot}/opt/%{name}/engine/

# Pre-built dashboard
cp -a dist/* %{buildroot}/opt/%{name}/dist/

# Data directory (empty profiles)
echo '{}' > %{buildroot}/opt/%{name}/data/profiles.json

# Setup wizard — goes in /usr/bin for easy access
install -d %{buildroot}%{_bindir}
install -m 755 rpm/wivi-sentinel-setup %{buildroot}%{_bindir}/wivi-sentinel-setup

# Systemd unit
install -d %{buildroot}%{_unitdir}
install -m 644 rpm/wivi-sentinel.service %{buildroot}%{_unitdir}/wivi-sentinel.service

# Firewall service definition (firewalld)
install -d %{buildroot}%{_prefix}/lib/firewalld/services
install -m 644 rpm/wivi-sentinel-firewall.xml %{buildroot}%{_prefix}/lib/firewalld/services/wivi-sentinel.xml

# tmpfiles.d — ensure /opt/wivi-sentinel/data survives
install -d %{buildroot}%{_tmpfilesdir}
echo "d /opt/%{name}/data 0755 %{name} %{name} -" > %{buildroot}%{_tmpfilesdir}/%{name}.conf

%pre
# Create service user (no login shell, home in /opt)
getent group %{name} >/dev/null || groupadd -r %{name}
getent passwd %{name} >/dev/null || \
    useradd -r -g %{name} -d /opt/%{name} -s /sbin/nologin \
    -c "Wi-Vi Sentinel service account" %{name}

# Add service user to dialout group for ESP32 serial access
usermod -aG dialout %{name} 2>/dev/null || true

%post
# ── Create Python venv and install deps ──────────────────────────────────────
echo ""
echo "  Installing Python dependencies (this may take a minute)..."
echo ""

VENV="/opt/%{name}/venv"
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install --upgrade pip --quiet 2>&1 | sed 's/^/  /'
"$VENV/bin/pip" install -r /opt/%{name}/requirements.txt --quiet 2>&1 | sed 's/^/  /'

# Fix ownership
chown -R %{name}:%{name} /opt/%{name}

# ── Reload systemd ───────────────────────────────────────────────────────────
systemctl daemon-reload

# ── Open firewall port ───────────────────────────────────────────────────────
if command -v firewall-cmd &>/dev/null && systemctl is-active firewalld &>/dev/null; then
    firewall-cmd --permanent --add-service=wivi-sentinel 2>/dev/null || \
    firewall-cmd --permanent --add-port=5555/tcp 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
fi

# ── Enable mDNS ─────────────────────────────────────────────────────────────
systemctl enable --now avahi-daemon 2>/dev/null || true

# ── Prompt user to run the setup wizard ──────────────────────────────────────
echo ""
echo "  ╔═══════════════════════════════════════════════════════════╗"
echo "  ║                                                           ║"
echo "  ║   Wi-Vi Sentinel installed successfully!                  ║"
echo "  ║                                                           ║"
echo "  ║   Run the setup wizard to configure WiFi and start:       ║"
echo "  ║                                                           ║"
echo "  ║       sudo wivi-sentinel-setup                            ║"
echo "  ║                                                           ║"
echo "  ╚═══════════════════════════════════════════════════════════╝"
echo ""

%preun
# Stop service before uninstall
if [ $1 -eq 0 ]; then
    systemctl stop wivi-sentinel.service 2>/dev/null || true
    systemctl disable wivi-sentinel.service 2>/dev/null || true
fi

%postun
# Full uninstall (not upgrade)
if [ $1 -eq 0 ]; then
    systemctl daemon-reload
    # Remove venv (but preserve data/)
    rm -rf /opt/%{name}/venv
    rm -rf /opt/%{name}/__pycache__
    rm -rf /opt/%{name}/engine/__pycache__

    # Remove firewall rule
    if command -v firewall-cmd &>/dev/null && systemctl is-active firewalld &>/dev/null; then
        firewall-cmd --permanent --remove-service=wivi-sentinel 2>/dev/null || true
        firewall-cmd --reload 2>/dev/null || true
    fi

    echo ""
    echo "  Wi-Vi Sentinel removed."
    echo "  Profile data preserved at /opt/%{name}/data/"
    echo "  To fully remove: rm -rf /opt/%{name}"
    echo ""
fi

%files
%defattr(-,%{name},%{name},-)

# App root
%dir /opt/%{name}
/opt/%{name}/server.py
/opt/%{name}/requirements.txt
/opt/%{name}/.env.example
/opt/%{name}/start.sh

# Engine
%dir /opt/%{name}/engine
/opt/%{name}/engine/*.py

# Dashboard
%dir /opt/%{name}/dist
/opt/%{name}/dist/*

# Data — mark as config so upgrades don't clobber profiles
%dir /opt/%{name}/data
%config(noreplace) /opt/%{name}/data/profiles.json

# Binaries
%{_bindir}/wivi-sentinel-setup

# Systemd
%{_unitdir}/wivi-sentinel.service

# Firewall
%{_prefix}/lib/firewalld/services/wivi-sentinel.xml

# tmpfiles
%{_tmpfilesdir}/%{name}.conf

%changelog
* Mon Mar 31 2026 Wi-Vi Sentinel <sentinel@wivi.local> - 2.0.0-1
- Initial RPM release with interactive WiFi setup wizard
- Bundled pre-built React dashboard
- ESP32 CSI auto-detection
- systemd service with auto-restart
- firewalld integration
