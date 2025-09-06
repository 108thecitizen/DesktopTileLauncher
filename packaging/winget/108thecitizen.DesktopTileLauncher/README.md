# WinGet manifests for DesktopTileLauncher

- Source of truth for our WinGet submission.
- To update for a new tag `vX.Y.Z`:
  1) Publish the release (EXE + onedir ZIP + SHA256SUMS.txt).
  2) `wingetcreate update 108thecitizen.DesktopTileLauncher -v X.Y.Z -u https://github.com/108thecitizen/DesktopTileLauncher/releases/download/vX.Y.Z/DesktopTileLauncher-vX.Y.Z-onedir-win-x64.zip -o .`
  3) Ensure InstallerType: zip, NestedInstallerType: portable, NestedInstallerFiles: DesktopTileLauncher.exe, and InstallerSha256 matches SHA256SUMS.txt.
  4) `winget validate .`
  5) `wingetcreate submit .`
