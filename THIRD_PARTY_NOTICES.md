# Third-party components

The application source is MIT licensed. Bundled components retain their own
licenses; the MIT license does not replace them.

- **7-Zip 26.04**, Igor Pavlov: LGPL, BSD portions and unRAR restrictions.
  Full notice: `third_party/7zip-License.txt`.
  Official source: https://github.com/ip7z/7zip/tree/26.04
  Official source download: https://www.7-zip.org/a/7z2604-src.tar.xz
  The application invokes the unmodified full engine as a separate process.
- **Qt for Python / PySide6 Essentials / Shiboken6 6.12.0**, The Qt Company:
  LGPLv3 and GPLv3 texts from the official PySide source are included as
  `third_party/Qt-LGPL-3.0.txt` and `third_party/Qt-GPL-3.0.txt`.
  Additional license files supplied in wheel metadata, when present, are copied
  into `third_party/package-licenses` during the build.
  Source and licensing: https://code.qt.io/cgit/pyside/pyside-setup.git/
  and https://doc.qt.io/qtforpython-6/licenses.html
- **PyInstaller 6.22.3**: package license and bootloader exception are included
  under `third_party/package-licenses` when supplied by the distribution.
  Source: https://github.com/pyinstaller/pyinstaller/tree/v6.22.3
- RAR regression fixtures originate from **rarfile**, Marko Kreen, and carry
  their ISC notice in `tests/fixtures/rar/LICENSE`. They are not bundled in the app.
- **pyzipper 0.4.0** (MIT) and **PyCryptodome / pycryptodomex 3.24.1**
  (BSD and public-domain portions) provide UTF-8 ZIP password compatibility.
  Their distribution license files are included in `third_party/package-licenses`.
  Sources: https://github.com/danifus/pyzipper and https://github.com/Legrandin/pycryptodome

Runtime binary paths are `vendor/7zip/windows/7z.exe` + `7z.dll`, or
`vendor/7zip/macos/7zz`, within the application's bundle. Platform-specific
builds include only the needed engine. Build scripts download official packages
and verify pinned SHA-256 checksums before extracting them.

**charset-normalizer 3.5.1** (MIT) is used to suggest legacy ZIP filename
encodings. Its distribution license is copied to `third_party/package-licenses`.
Source: https://github.com/jawah/charset_normalizer
