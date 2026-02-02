#!/usr/bin/env python3
"""
BioKGrapher Setup Script
========================

This script helps set up BioKGrapher with sample data for testing
or validates the environment for production use.

Usage:
    python setup.py --help
    python setup.py --check           # Check environment and dependencies
    python setup.py --create-sample-data  # Create sample data for testing
    python setup.py --full-setup      # Run full setup (check + sample data)
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path

# Get the directory where this script is located
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_DIR = SCRIPT_DIR.parent


def check_python_version():
    """Check if Python version is compatible."""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print(f"  [FAIL] Python {version.major}.{version.minor} detected. Python 3.8+ required.")
        return False
    print(f"  [OK] Python {version.major}.{version.minor}.{version.micro}")
    return True


def check_dependencies():
    """Check if required Python packages are installed."""
    required_packages = [
        ('pywebio', 'pywebio'),
        ('pandas', 'pandas'),
        ('plotly', 'plotly.express'),
        ('tqdm', 'tqdm'),
    ]

    optional_packages = [
        ('medcat', 'medcat'),
        ('lxml', 'lxml'),
    ]

    all_installed = True

    print("\nRequired packages:")
    for package_name, import_name in required_packages:
        try:
            __import__(import_name.split('.')[0])
            print(f"  [OK] {package_name}")
        except ImportError:
            print(f"  [MISSING] {package_name}")
            all_installed = False

    print("\nOptional packages (for indexing):")
    for package_name, import_name in optional_packages:
        try:
            __import__(import_name.split('.')[0])
            print(f"  [OK] {package_name}")
        except ImportError:
            print(f"  [MISSING] {package_name} (optional - needed for PubMed indexing)")

    return all_installed


def check_directory_structure():
    """Check if required directories exist."""
    required_dirs = [
        SCRIPT_DIR / 'data',
        SCRIPT_DIR / 'presets',
        SCRIPT_DIR / 'template',
        PROJECT_DIR / 'models',
        PROJECT_DIR / 'index',
    ]

    print("\nDirectory structure:")
    all_exist = True
    for directory in required_dirs:
        if directory.exists():
            print(f"  [OK] {directory.relative_to(PROJECT_DIR)}")
        else:
            print(f"  [MISSING] {directory.relative_to(PROJECT_DIR)}")
            # Create missing directories
            directory.mkdir(parents=True, exist_ok=True)
            print(f"         -> Created")

    return True


def check_data_files():
    """Check if required data files exist."""
    required_files = [
        (SCRIPT_DIR / 'config.conf', 'Configuration file'),
        (SCRIPT_DIR / 'template' / 'tree_view_template.html', 'HTML template'),
    ]

    data_files = [
        (SCRIPT_DIR / 'new_full_kld_cache_with_titles.pkl', 'KLD cache file'),
        (SCRIPT_DIR / 'definitions.txt', 'Definitions file'),
        (SCRIPT_DIR / 'data' / 'MRCONSO.RRF', 'MRCONSO.RRF'),
        (SCRIPT_DIR / 'data' / 'MRDEF.RRF', 'MRDEF.RRF'),
        (SCRIPT_DIR / 'data' / 'MRHIER.RRF', 'MRHIER.RRF'),
        (SCRIPT_DIR / 'data' / 'MRREL.RRF', 'MRREL.RRF'),
    ]

    print("\nRequired files:")
    for filepath, description in required_files:
        if filepath.exists():
            print(f"  [OK] {description}")
        else:
            print(f"  [MISSING] {description}")

    print("\nData files:")
    missing_data = []
    for filepath, description in data_files:
        if filepath.exists():
            # Check if it's a placeholder
            with open(filepath, 'rb') as f:
                content = f.read(20)
            if b'#placeholder' in content:
                print(f"  [PLACEHOLDER] {description}")
                missing_data.append(description)
            else:
                size = filepath.stat().st_size
                print(f"  [OK] {description} ({size / 1024:.1f} KB)")
        else:
            print(f"  [MISSING] {description}")
            missing_data.append(description)

    return len(missing_data) == 0


def check_environment():
    """Run all environment checks."""
    print("=" * 60)
    print("BioKGrapher Environment Check")
    print("=" * 60)

    results = {
        'python': check_python_version(),
        'dependencies': check_dependencies(),
        'directories': check_directory_structure(),
        'data_files': check_data_files(),
    }

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    all_good = all(results.values())

    for check, passed in results.items():
        status = "PASS" if passed else "FAIL/INCOMPLETE"
        print(f"  {check}: {status}")

    if not all_good:
        print("\n" + "-" * 60)
        print("Some checks failed or are incomplete.")
        print("\nTo create sample data for testing, run:")
        print("  python setup.py --create-sample-data")
        print("\nTo install missing dependencies, run:")
        print("  pip install -r requirements.txt")

    return all_good


def create_sample_data():
    """Create sample data files for testing."""
    print("=" * 60)
    print("Creating Sample Data")
    print("=" * 60)

    # Create sample UMLS files
    print("\n1. Creating sample UMLS files...")
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / 'build_cache.py'), '--sample'],
            cwd=str(SCRIPT_DIR),
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("   [OK] Sample UMLS files created")
        else:
            print(f"   [FAIL] {result.stderr}")
            return False
    except Exception as e:
        print(f"   [FAIL] {e}")
        return False

    # Create sample index
    print("\n2. Creating sample PubMed index...")
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / 'index_pubmed.py'), '--sample'],
            cwd=str(SCRIPT_DIR),
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("   [OK] Sample index created")
        else:
            print(f"   [FAIL] {result.stderr}")
            return False
    except Exception as e:
        print(f"   [FAIL] {e}")
        return False

    print("\n" + "=" * 60)
    print("Sample data created successfully!")
    print("=" * 60)
    print("\nYou can now run the application:")
    print(f"  cd {SCRIPT_DIR}")
    print("  python web_app.py")
    print("\nThen open your browser to: http://localhost:6006")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="BioKGrapher Setup Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Check environment
    python setup.py --check

    # Create sample data for testing
    python setup.py --create-sample-data

    # Full setup (check + sample data)
    python setup.py --full-setup
        """
    )

    parser.add_argument(
        '--check',
        action='store_true',
        help='Check environment and dependencies'
    )
    parser.add_argument(
        '--create-sample-data',
        action='store_true',
        help='Create sample data files for testing'
    )
    parser.add_argument(
        '--full-setup',
        action='store_true',
        help='Run full setup (check + sample data)'
    )

    args = parser.parse_args()

    if not any([args.check, args.create_sample_data, args.full_setup]):
        parser.print_help()
        print("\nNo action specified. Use --help for usage information.")
        sys.exit(1)

    success = True

    if args.check or args.full_setup:
        success = check_environment()

    if args.create_sample_data or args.full_setup:
        success = create_sample_data() and success

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
