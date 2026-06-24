Name:           codex-gui
Version:        0.1.0
Release:        1%{?dist}
Summary:        Unofficial native Fedora desktop client for the official Codex CLI

License:        MIT
URL:            https://example.invalid/codex-gui
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
Requires:       python3-gobject
Requires:       gtk3
Requires:       vte291
Requires:       nodejs
Requires:       npm

%description
Codex Studio is an unofficial GTK/VTE desktop client for the official @openai/codex CLI.
It embeds the CLI in a real PTY and adds a Fedora desktop launcher, workspace
picker, settings, session dashboard, login launcher, review, resume, doctor,
and one-off task dialog.

%prep
%autosetup

%build
%py3_build

%install
%py3_install
install -Dpm0644 data/codex-gui.desktop %{buildroot}%{_datadir}/applications/codex-gui.desktop

%files
%license README.md
%doc README.md
%{_bindir}/codex-gui
%{python3_sitelib}/codex_gui*
%{python3_sitelib}/codex_gui_fedora-*.dist-info
%{_datadir}/applications/codex-gui.desktop

%changelog
* Sun Jun 21 2026 Codex GUI Maintainers <noreply@example.invalid> - 0.1.0-1
- Initial Fedora GTK/VTE wrapper.
