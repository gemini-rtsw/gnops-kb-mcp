# gnops-kb-mcp: MCP server for the team's shared Claude notes, shipped as a
# container for the gemini-rtsw-ci pipeline. The RPM installs a systemd unit;
# the unit pulls and runs the image built from this same commit.

# $GIT_HASH is passed in by build_rpm.sh; git is only the fallback.
%define git_hash %(if [ -n "$GIT_HASH" ]; then echo "$GIT_HASH"; else git rev-parse --short HEAD 2>/dev/null || echo nogit; fi)
%global appimage ghcr.io/gemini-rtsw/gnops-kb-mcp

Name:      gnops-kb-mcp
Version:   0.1.0
Release:   1.git%{git_hash}%{?dist}
Summary:   MCP server serving the team's shared notes (briefings) to Claude sessions
License:   Proprietary
Source0:   %{name}-%{version}.tar.gz
BuildArch: noarch
BuildRequires: systemd-rpm-macros
Requires:  systemd

%description
Runs the gnops-kb-mcp container as a systemd service. It serves an MCP endpoint
(http://<host>:8002/mcp) that gives Claude sessions the team's briefings and
notes from a git repo, and lets sessions propose note updates as pull
requests for review. Notes live in /var/lib/gnops-kb-mcp.

%prep
%autosetup

%build
# Pin the unit to the exact image built alongside this RPM.
sed -e 's|@IMAGE@|%{appimage}:%{version}-git%{git_hash}|' deploy/%{name}.service.in > %{name}.service
if grep -q '@IMAGE@' %{name}.service; then echo "ERROR: placeholder not substituted" >&2; exit 1; fi

%install
install -Dpm 0644 %{name}.service %{buildroot}%{_unitdir}/%{name}.service
# 0600: holds the GitHub token for the notes repo.
install -Dpm 0600 deploy/%{name}.sysconfig %{buildroot}%{_sysconfdir}/sysconfig/%{name}
install -dm 0750 %{buildroot}%{_sharedstatedir}/%{name}

%post
%systemd_post %{name}.service
%preun
%systemd_preun %{name}.service
%postun
%systemd_postun %{name}.service

%files
# Not a config file: the unit carries the image tag, so upgrades must overwrite it.
%{_unitdir}/%{name}.service
%config(noreplace) %{_sysconfdir}/sysconfig/%{name}
# The container runs as nobody and keeps the notes checkout here.
%dir %attr(0750,nobody,nobody) %{_sharedstatedir}/%{name}

%changelog
* Thu Oct 29 2026 Hawi Stecher <hawi.stecher@noirlab.edu> - 0.1.0-1
- Initial package: briefings, notes search, distill prompts and
  pull-request proposals over MCP; /start for a SessionStart hook.
