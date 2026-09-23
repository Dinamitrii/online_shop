"""Run from the shop Python environment: python optimize_images.py."""
from pathlib import Path
from responsive_images import generate


def main():
    root = Path(__file__).resolve().parent
    folder = root / 'static/img/products'
    paths = sorted(p for p in folder.iterdir() if p.is_file() and
                   p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif'})
    paths.append(root / 'static/img/brand/logo.png')
    failed = []
    for path in paths:
        if not generate(path):
            failed.append(path.name)
    print(f'Optimized: {len(paths) - len(failed)} / {len(paths)}')
    for name in failed:
        print(f'Skipped (missing, animated or unreadable): {name}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
