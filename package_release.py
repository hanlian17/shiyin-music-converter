"""Build a clean, self-contained Shiyin ZIP from the PyInstaller output."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = '1.2.1'
SOURCE_FILES = (
    'app.py', 'converter.py', 'formats.py', 'qmc_crypto.py', 'qq_session.py',
    'qmc_native.c', 'qmc_native.dll', 'requirements.txt', 'build.ps1',
    'package_release.py', 'README.md', 'LICENSE', 'THIRD_PARTY.md', '.gitignore',
)


def main():
    built = ROOT / 'dist' / 'Shiyin'
    if not (built / 'Shiyin.exe').is_file():
        raise SystemExit('Build app.py with PyInstaller --name Shiyin --onedir first.')
    release = ROOT / 'release'
    release.mkdir(exist_ok=True)
    archive = release / f'Shiyin-{VERSION}-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        bundle.write(built / 'Shiyin.exe', 'Shiyin/Shiyin.exe')
        for path in sorted((built / '_internal').rglob('*')):
            if path.is_file():
                bundle.write(path, Path('Shiyin') / path.relative_to(built))
        bundle.writestr('Shiyin/data/settings.json', json.dumps(
            {'source': '', 'target': 'FLAC（无损）', 'bitrate': 320},
            ensure_ascii=False, indent=2))
        for name in ('README.md', 'LICENSE', 'THIRD_PARTY.md'):
            bundle.write(ROOT / name, f'Shiyin/{name}')
        for name in SOURCE_FILES:
            bundle.write(ROOT / name, f'Shiyin/source/{name}')
        bundle.write(ROOT / 'tests' / 'test_converter.py',
                     'Shiyin/source/tests/test_converter.py')
        for path in sorted((ROOT / 'licenses').rglob('*')):
            if path.is_file():
                relative = path.relative_to(ROOT)
                bundle.write(path, Path('Shiyin') / relative)
                bundle.write(path, Path('Shiyin/source') / relative)
    with archive.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', 'ascii')
    print(archive)
    print(f'{archive.stat().st_size / 1024 / 1024:.1f} MiB')
    print(f'SHA256 {digest}')


if __name__ == '__main__':
    main()
