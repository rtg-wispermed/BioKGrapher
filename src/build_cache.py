#!/usr/bin/env python3
"""
UMLS Cache Builder for BioKGrapher
==================================

This module builds the necessary cache files from UMLS Metathesaurus files:
1. definitions.txt - CUI to preferred term mapping
2. Validates UMLS RRF files (MRCONSO, MRDEF, MRHIER, MRREL)

Prerequisites:
- Valid UMLS license
- UMLS Metathesaurus files downloaded and placed in src/data/

Usage:
    python build_cache.py --help
    python build_cache.py --build-definitions    # Build definitions.txt from UMLS
    python build_cache.py --validate             # Validate UMLS files
    python build_cache.py --sample               # Create sample files for testing
"""

import os
import sys
import csv
import argparse
from pathlib import Path
from collections import defaultdict
from typing import Dict, Set, Optional

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

# Get the directory where this script is located
SCRIPT_DIR = Path(__file__).parent.absolute()
DATA_DIR = SCRIPT_DIR / "data"

# UMLS file paths
MRCONSO_FILE = DATA_DIR / "MRCONSO.RRF"
MRDEF_FILE = DATA_DIR / "MRDEF.RRF"
MRHIER_FILE = DATA_DIR / "MRHIER.RRF"
MRREL_FILE = DATA_DIR / "MRREL.RRF"

# Output files
DEFINITIONS_FILE = SCRIPT_DIR / "definitions.txt"


def check_umls_files() -> Dict[str, bool]:
    """
    Check which UMLS files are present and valid.

    Returns:
        Dictionary mapping file name to validity status
    """
    results = {}

    files_to_check = {
        'MRCONSO.RRF': MRCONSO_FILE,
        'MRDEF.RRF': MRDEF_FILE,
        'MRHIER.RRF': MRHIER_FILE,
        'MRREL.RRF': MRREL_FILE,
    }

    for name, path in files_to_check.items():
        if not path.exists():
            results[name] = False
            continue

        # Check if it's a placeholder file
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            first_line = f.readline().strip()

        if first_line.startswith('#placeholder') or first_line == '':
            results[name] = False
        else:
            results[name] = True

    return results


def validate_umls_files():
    """
    Validate that all required UMLS files are present and properly formatted.
    """
    print("\n" + "=" * 60)
    print("Validating UMLS Files")
    print("=" * 60)

    status = check_umls_files()

    all_valid = True
    for name, valid in status.items():
        status_str = "OK" if valid else "MISSING/INVALID"
        symbol = "✓" if valid else "✗"
        print(f"  {symbol} {name}: {status_str}")
        if not valid:
            all_valid = False

    if not all_valid:
        print("\n" + "-" * 60)
        print("Some UMLS files are missing or contain placeholder data.")
        print("\nTo obtain UMLS files:")
        print("1. Get a UMLS license from: https://www.nlm.nih.gov/databases/umls.html")
        print("2. Download the UMLS Metathesaurus from:")
        print("   https://www.nlm.nih.gov/research/umls/licensedcontent/umlsknowledgesources.html")
        print("3. Extract the RRF files and copy them to: src/data/")
        print("\nAlternatively, run: python build_cache.py --sample")
        print("to create sample files for testing.")
        return False

    print("\nAll UMLS files validated successfully!")
    return True


def build_definitions_from_mrconso():
    """
    Build definitions.txt from MRCONSO.RRF.

    The definitions.txt file maps CUI to preferred term (concept name).
    Format: CUI<space>Preferred_Term

    MRCONSO.RRF format (pipe-delimited):
    0: CUI - Concept Unique Identifier
    1: LAT - Language of Term
    2: TS - Term Status (P=Preferred, S=Synonym)
    3: LUI - Lexical Unique Identifier
    4: STT - String Type
    5: SUI - String Unique Identifier
    6: ISPREF - Is Preferred in Source (Y/N)
    7: AUI - Atom Unique Identifier
    8-10: SAUI, SCUI, SDUI
    11: SAB - Source Abbreviation
    12: TTY - Term Type in Source
    13: CODE - Source code
    14: STR - String (the actual term)
    15+: Additional fields
    """
    print("\n" + "=" * 60)
    print("Building definitions.txt from MRCONSO.RRF")
    print("=" * 60)

    if not MRCONSO_FILE.exists():
        print(f"Error: MRCONSO.RRF not found at {MRCONSO_FILE}")
        return False

    # Check if placeholder
    with open(MRCONSO_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        first_line = f.readline()
        if first_line.startswith('#placeholder'):
            print("Error: MRCONSO.RRF is a placeholder file.")
            print("Please download the actual UMLS file.")
            return False

    print(f"Reading: {MRCONSO_FILE}")

    # We want to get the preferred English term for each CUI
    # Priority: ENG language, P (Preferred) term status, Y (is preferred in source)
    cui_to_term = {}
    cui_to_score = {}  # Track the best score for each CUI

    def get_term_score(lat, ts, ispref):
        """Calculate a priority score for term selection."""
        score = 0
        if lat == 'ENG':
            score += 100
        if ts == 'P':
            score += 10
        if ispref == 'Y':
            score += 1
        return score

    # Count lines for progress bar
    print("Counting entries...")
    total_lines = sum(1 for _ in open(MRCONSO_FILE, 'r', encoding='utf-8', errors='ignore'))

    print(f"Processing {total_lines:,} entries...")

    with open(MRCONSO_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        for line in tqdm(f, total=total_lines, desc="Processing MRCONSO"):
            parts = line.split('|')
            if len(parts) < 15:
                continue

            cui = parts[0]
            lat = parts[1]  # Language
            ts = parts[2]   # Term Status
            ispref = parts[6]  # Is Preferred
            term = parts[14]  # String/Term

            if not cui or not term:
                continue

            score = get_term_score(lat, ts, ispref)

            # Update if this is a better term
            if cui not in cui_to_score or score > cui_to_score[cui]:
                cui_to_term[cui] = term
                cui_to_score[cui] = score

    print(f"\nExtracted {len(cui_to_term):,} unique concepts")

    # Write definitions file
    print(f"Writing: {DEFINITIONS_FILE}")
    with open(DEFINITIONS_FILE, 'w', encoding='utf-8') as f:
        for cui, term in sorted(cui_to_term.items()):
            # Escape special characters and remove newlines
            clean_term = term.replace('\n', ' ').replace('\r', ' ').strip()
            f.write(f"{cui} {clean_term}\n")

    print(f"definitions.txt created successfully!")
    print(f"  - Size: {DEFINITIONS_FILE.stat().st_size / 1024 / 1024:.2f} MB")
    print(f"  - Concepts: {len(cui_to_term):,}")

    return True


def create_sample_umls_files():
    """
    Create sample UMLS files for testing purposes.

    These are minimal valid files that allow the application to start
    and demonstrate functionality without requiring actual UMLS data.
    """
    print("\n" + "=" * 60)
    print("Creating Sample UMLS Files for Testing")
    print("=" * 60)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Sample CUIs and their data
    sample_concepts = [
        ('C0025202', 'A0123456', 'Melanoma', 'Melanoma is a type of cancer that develops from the pigment-producing cells known as melanocytes.'),
        ('C0011581', 'A0234567', 'Depression', 'A mental state characterized by feelings of sadness, loneliness, despair, low self-esteem, and self-reproach.'),
        ('C0011849', 'A0345678', 'Diabetes Mellitus', 'A heterogeneous group of disorders characterized by hyperglycemia and glucose intolerance.'),
        ('C0007102', 'A0456789', 'Colon Cancer', 'A malignant neoplasm arising from the epithelial cells lining the colon.'),
        ('C0027051', 'A0567890', 'Myocardial Infarction', 'Necrosis of the myocardium caused by an interruption of the blood supply.'),
        ('C0007124', 'A0678901', 'Breast Cancer', 'A malignant neoplasm that originates in the breast tissue.'),
        ('C0002736', 'A0789012', 'Amyotrophic Lateral Sclerosis', 'A progressive neurodegenerative disease affecting motor neurons.'),
        ('C0004096', 'A0890123', 'Asthma', 'A chronic respiratory condition marked by bronchospasm and excessive mucus production.'),
        ('C0020538', 'A0901234', 'Hypertension', 'Persistently high arterial blood pressure.'),
        ('C0038454', 'A0012345', 'Stroke', 'An acute neurological event characterized by sudden loss of brain function.'),
        ('C0024117', 'A0123457', 'COPD', 'A disease characterized by airflow limitation that is not fully reversible.'),
        ('C0022116', 'A0234568', 'Ischemia', 'A restriction in blood supply to tissues causing a shortage of oxygen.'),
        ('C0003873', 'A0345679', 'Arthritis', 'Inflammation of a joint accompanied by pain, swelling, and stiffness.'),
        ('C0030567', 'A0456780', 'Parkinson Disease', 'A progressive neurodegenerative disorder characterized by tremor and rigidity.'),
        ('C0002395', 'A0567891', 'Alzheimer Disease', 'A progressive form of dementia that affects memory, thinking and behavior.'),
        ('C0006826', 'A0678902', 'Cancer', 'A general term for diseases in which abnormal cells divide without control.'),
        ('C0027947', 'A0789013', 'Neoplasm', 'An abnormal mass of tissue that results from excessive cell division.'),
        ('C0012634', 'A0890124', 'Disease', 'A pathological condition of a body part, an organ, or a system.'),
        ('C0037274', 'A0901235', 'Skin Neoplasm', 'A neoplasm arising from skin tissue.'),
        ('C0153550', 'A0012346', 'Malignant Melanoma', 'A melanoma that has the potential to metastasize.'),
    ]

    # Create MRCONSO.RRF (concept names)
    print(f"Creating: {MRCONSO_FILE}")
    with open(MRCONSO_FILE, 'w', encoding='utf-8') as f:
        for cui, aui, name, _ in sample_concepts:
            # Format: CUI|LAT|TS|LUI|STT|SUI|ISPREF|AUI|SAUI|SCUI|SDUI|SAB|TTY|CODE|STR|...
            f.write(f"{cui}|ENG|P|L0001|PF|S0001|Y|{aui}|||{cui}|NCI|PT|{cui}|{name}||\n")
    print(f"  Created with {len(sample_concepts)} concepts")

    # Create MRDEF.RRF (definitions)
    print(f"Creating: {MRDEF_FILE}")
    with open(MRDEF_FILE, 'w', encoding='utf-8') as f:
        for cui, aui, name, definition in sample_concepts:
            # Format: CUI|AUI|ATUI|SATUI|SAB|DEF|...
            f.write(f"{cui}|{aui}|AT001|SAT001|NCI|{definition}||\n")
    print(f"  Created with {len(sample_concepts)} definitions")

    # Create MRHIER.RRF (hierarchies)
    # Build a simple hierarchy: Disease -> Cancer -> specific cancers
    print(f"Creating: {MRHIER_FILE}")
    hierarchies = [
        # Melanoma hierarchy: Disease -> Neoplasm -> Skin Neoplasm -> Melanoma -> Malignant Melanoma
        ('C0025202', 'A0123456', 'SNOMEDCT_US', 'A0012634.A0789013.A0901235'),
        ('C0153550', 'A0012346', 'SNOMEDCT_US', 'A0012634.A0789013.A0901235.A0123456'),
        # Depression: Disease -> Depression
        ('C0011581', 'A0234567', 'SNOMEDCT_US', 'A0012634'),
        # Diabetes: Disease -> Diabetes
        ('C0011849', 'A0345678', 'SNOMEDCT_US', 'A0012634'),
        # Colon Cancer: Disease -> Neoplasm -> Cancer -> Colon Cancer
        ('C0007102', 'A0456789', 'SNOMEDCT_US', 'A0012634.A0789013.A0678902'),
        # Breast Cancer: Disease -> Neoplasm -> Cancer -> Breast Cancer
        ('C0007124', 'A0678901', 'SNOMEDCT_US', 'A0012634.A0789013.A0678902'),
        # Also add NCI terminology entries
        ('C0025202', 'A0123456', 'NCI', 'A0012634.A0789013.A0901235'),
        ('C0011581', 'A0234567', 'NCI', 'A0012634'),
        ('C0011849', 'A0345678', 'NCI', 'A0012634'),
        ('C0007102', 'A0456789', 'NCI', 'A0012634.A0789013.A0678902'),
    ]

    with open(MRHIER_FILE, 'w', encoding='utf-8') as f:
        for cui, aui, sab, path in hierarchies:
            # Format: CUI|AUI|CXN|PAUI|SAB|RELA|PTR|...
            f.write(f"{cui}|{aui}|1||{sab}||{path}||\n")
    print(f"  Created with {len(hierarchies)} hierarchy entries")

    # Create MRREL.RRF (relationships)
    print(f"Creating: {MRREL_FILE}")
    relationships = [
        # Melanoma is-a Skin Neoplasm
        ('C0025202', 'C0037274', 'isa'),
        # Malignant Melanoma is-a Melanoma
        ('C0153550', 'C0025202', 'isa'),
        # Colon Cancer is-a Cancer
        ('C0007102', 'C0006826', 'isa'),
        # Breast Cancer is-a Cancer
        ('C0007124', 'C0006826', 'isa'),
        # Cancer is-a Neoplasm
        ('C0006826', 'C0027947', 'isa'),
        # Neoplasm is-a Disease
        ('C0027947', 'C0012634', 'isa'),
    ]

    with open(MRREL_FILE, 'w', encoding='utf-8') as f:
        for cui1, cui2, rel in relationships:
            # Format: CUI1|AUI1|STYPE1|REL|CUI2|AUI2|STYPE2|RELA|RUI|SRUI|SAB|SL|RG|DIR|SUPPRESS|CVF|
            f.write(f"{cui1}|||{rel}|{cui2}|||||NCI|NCI|||N||\n")
    print(f"  Created with {len(relationships)} relationships")

    # Create definitions.txt
    print(f"Creating: {DEFINITIONS_FILE}")
    with open(DEFINITIONS_FILE, 'w', encoding='utf-8') as f:
        for cui, aui, name, _ in sample_concepts:
            f.write(f"{cui} {name}\n")
    print(f"  Created with {len(sample_concepts)} definitions")

    print("\nSample UMLS files created successfully!")
    print("\nNote: These are minimal sample files for testing.")
    print("For production use, download actual UMLS files with a valid license.")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Build cache files from UMLS for BioKGrapher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Validate UMLS files
    python build_cache.py --validate

    # Build definitions.txt from MRCONSO.RRF
    python build_cache.py --build-definitions

    # Create sample files for testing (no UMLS license required)
    python build_cache.py --sample

    # Do everything
    python build_cache.py --validate --build-definitions
        """
    )

    parser.add_argument(
        '--validate',
        action='store_true',
        help='Validate that UMLS files are present and properly formatted'
    )
    parser.add_argument(
        '--build-definitions',
        action='store_true',
        help='Build definitions.txt from MRCONSO.RRF'
    )
    parser.add_argument(
        '--sample',
        action='store_true',
        help='Create sample UMLS files for testing (no license required)'
    )

    args = parser.parse_args()

    if not any([args.validate, args.build_definitions, args.sample]):
        parser.print_help()
        print("\nError: Please specify at least one action.")
        sys.exit(1)

    success = True

    if args.sample:
        success = create_sample_umls_files()

    if args.validate:
        success = validate_umls_files() and success

    if args.build_definitions and success:
        # Only build if validation passed or if we just created sample files
        success = build_definitions_from_mrconso() and success

    if success:
        print("\n" + "=" * 60)
        print("Done!")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("Completed with errors. Check messages above.")
        print("=" * 60)
        sys.exit(1)


if __name__ == '__main__':
    main()
