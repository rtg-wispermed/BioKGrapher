#!/usr/bin/env python3
"""
PubMed Indexing Module for BioKGrapher
======================================

This module handles:
1. Downloading PubMed baseline and update files
2. Parsing PubMed XML files to extract abstracts
3. Running MedCAT NER+NEL on abstracts to extract UMLS concepts
4. Building the KLD cache file (pmid_to_concepts mapping)

Usage:
    python index_pubmed.py --help
    python index_pubmed.py --download         # Download PubMed XML files
    python index_pubmed.py --index            # Index downloaded files
    python index_pubmed.py --download --index # Do both
"""

import os
import sys
import gzip
import glob
import pickle
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional, Generator

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

# lxml is only required for XML parsing (not for sample data generation)
etree = None
def _check_lxml():
    global etree
    if etree is None:
        try:
            from lxml import etree as _etree
            etree = _etree
        except ImportError:
            print("Error: lxml is required for XML parsing. Install with: pip install lxml")
            sys.exit(1)
    return etree

# Get the directory where this script is located
SCRIPT_DIR = Path(__file__).parent.absolute()
INDEX_DIR = SCRIPT_DIR.parent / "index"
BASELINE_DIR = INDEX_DIR / "baseline"
UPDATEFILES_DIR = INDEX_DIR / "updatefiles"
MODELS_DIR = SCRIPT_DIR.parent / "models"

# Output file path
OUTPUT_CACHE_FILE = SCRIPT_DIR / "new_full_kld_cache_with_titles.pkl"


def ensure_directories():
    """Create necessary directories if they don't exist."""
    for directory in [INDEX_DIR, BASELINE_DIR, UPDATEFILES_DIR, MODELS_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
        print(f"  Directory ready: {directory}")


def download_pubmed_files(include_updates: bool = False):
    """
    Download PubMed baseline files and optionally update files.

    Uses wget to download files from NCBI FTP server.
    """
    print("\n" + "=" * 60)
    print("Downloading PubMed Files")
    print("=" * 60)

    ensure_directories()

    # Download baseline files
    print(f"\nDownloading baseline files to: {BASELINE_DIR}")
    print("This will download ~350GB of compressed XML files.")
    print("Press Ctrl+C to cancel.\n")

    baseline_cmd = [
        "wget", "-nc", "-P", str(BASELINE_DIR),
        "ftp://ftp.ncbi.nlm.nih.gov/pubmed/baseline/*.xml.gz"
    ]

    try:
        subprocess.run(baseline_cmd, check=True, cwd=str(BASELINE_DIR))
        print("Baseline download complete!")
    except subprocess.CalledProcessError as e:
        print(f"Warning: wget returned error code {e.returncode}")
        print("Some files may have failed to download. Re-run to retry.")
    except FileNotFoundError:
        print("Error: wget not found. Please install wget or download files manually.")
        print(f"Manual download: ftp://ftp.ncbi.nlm.nih.gov/pubmed/baseline/")
        return False

    # Download update files if requested
    if include_updates:
        print(f"\nDownloading update files to: {UPDATEFILES_DIR}")

        update_cmd = [
            "wget", "-nc", "-P", str(UPDATEFILES_DIR),
            "ftp://ftp.ncbi.nlm.nih.gov/pubmed/updatefiles/*.xml.gz"
        ]

        try:
            subprocess.run(update_cmd, check=True, cwd=str(UPDATEFILES_DIR))
            print("Update files download complete!")
        except subprocess.CalledProcessError as e:
            print(f"Warning: Some update files may have failed to download.")

    return True


def parse_pubmed_xml(xml_file: Path) -> Generator[Dict, None, None]:
    """
    Parse a PubMed XML file and yield article data.

    Args:
        xml_file: Path to the gzipped XML file

    Yields:
        Dictionary with PMID, title, and abstract for each article
    """
    # Ensure lxml is available
    etree = _check_lxml()

    try:
        # Handle both gzipped and plain XML
        if str(xml_file).endswith('.gz'):
            open_func = gzip.open
        else:
            open_func = open

        with open_func(xml_file, 'rb') as f:
            # Use iterparse for memory efficiency
            context = etree.iterparse(f, events=('end',), tag='PubmedArticle')

            for event, article in context:
                try:
                    pmid_elem = article.find('.//PMID')
                    if pmid_elem is None:
                        continue
                    pmid = pmid_elem.text

                    # Get title
                    title_elem = article.find('.//ArticleTitle')
                    title = title_elem.text if title_elem is not None and title_elem.text else ""

                    # Get abstract text (may have multiple AbstractText elements)
                    abstract_parts = []
                    for abs_elem in article.findall('.//AbstractText'):
                        if abs_elem.text:
                            # Handle labeled sections
                            label = abs_elem.get('Label', '')
                            if label:
                                abstract_parts.append(f"{label}: {abs_elem.text}")
                            else:
                                abstract_parts.append(abs_elem.text)

                    abstract = ' '.join(abstract_parts)

                    if pmid and (title or abstract):
                        yield {
                            'pmid': pmid,
                            'title': title,
                            'abstract': abstract,
                            'text': f"{title}. {abstract}" if title and abstract else title or abstract
                        }

                except Exception as e:
                    continue
                finally:
                    # Clear element to free memory
                    article.clear()
                    while article.getprevious() is not None:
                        del article.getparent()[0]

    except Exception as e:
        print(f"Error parsing {xml_file}: {e}")


def load_medcat_model() -> Optional[object]:
    """
    Load the MedCAT model for NER+NEL.

    Returns:
        MedCAT CAT object or None if loading fails
    """
    try:
        from medcat.cat import CAT
    except ImportError:
        print("Error: MedCAT is not installed. Install with: pip install medcat")
        return None

    # Look for model files in the models directory
    model_files = list(MODELS_DIR.glob("*"))

    if not model_files:
        print(f"\nError: No MedCAT model found in {MODELS_DIR}")
        print("\nTo obtain a model:")
        print("1. Sign into your NIH/UMLS account")
        print("2. Download a public MedCAT model from:")
        print("   https://uts.nlm.nih.gov/uts/login?service=https://medcat.rosalind.kcl.ac.uk/auth-callback")
        print("3. Unzip the model into the models/ folder")
        return None

    # Try to find a model pack (directory or zip file)
    model_path = None
    for item in model_files:
        if item.is_dir() and (item / 'model_card.json').exists():
            model_path = item
            break
        elif item.suffix == '.zip':
            model_path = item
            break

    if model_path is None:
        # Try the first directory
        for item in model_files:
            if item.is_dir():
                model_path = item
                break

    if model_path is None:
        print(f"Error: Could not find a valid MedCAT model in {MODELS_DIR}")
        return None

    print(f"Loading MedCAT model from: {model_path}")

    try:
        cat = CAT.load_model_pack(str(model_path))
        print("MedCAT model loaded successfully!")
        return cat
    except Exception as e:
        print(f"Error loading MedCAT model: {e}")
        return None


def extract_concepts_from_text(cat, text: str) -> List[str]:
    """
    Extract UMLS concepts from text using MedCAT.

    Args:
        cat: MedCAT CAT object
        text: Text to process

    Returns:
        List of CUI (Concept Unique Identifier) strings
    """
    if not text or not cat:
        return []

    try:
        # Get entities from text
        entities = cat.get_entities(text)

        # Extract CUIs from entities
        cuis = []
        if 'entities' in entities:
            for ent_id, ent_data in entities['entities'].items():
                cui = ent_data.get('cui')
                if cui:
                    cuis.append(cui)

        return cuis
    except Exception as e:
        return []


def process_xml_file(xml_file: Path, cat) -> Dict[str, Dict]:
    """
    Process a single XML file and extract concepts from all articles.

    Args:
        xml_file: Path to the XML file
        cat: MedCAT model

    Returns:
        Dictionary mapping PMID to article data with concepts
    """
    results = {}

    for article in parse_pubmed_xml(xml_file):
        pmid = article['pmid']
        concepts = extract_concepts_from_text(cat, article['text'])

        if concepts:
            results[pmid] = {
                'title': article['title'],
                'concepts': concepts
            }

    return results


def index_pubmed_files(num_workers: int = 4, max_files: int = None):
    """
    Index all PubMed XML files to create the KLD cache.

    Args:
        num_workers: Number of parallel workers for processing
        max_files: Maximum number of files to process (None for all)
    """
    print("\n" + "=" * 60)
    print("Indexing PubMed Files")
    print("=" * 60)

    # Collect all XML files
    xml_files = []
    for directory in [BASELINE_DIR, UPDATEFILES_DIR]:
        xml_files.extend(list(directory.glob("*.xml.gz")))
        xml_files.extend(list(directory.glob("*.xml")))

    if not xml_files:
        print(f"\nNo XML files found in:")
        print(f"  - {BASELINE_DIR}")
        print(f"  - {UPDATEFILES_DIR}")
        print("\nRun with --download first to download PubMed files.")
        return False

    print(f"\nFound {len(xml_files)} XML files to process")

    if max_files:
        xml_files = xml_files[:max_files]
        print(f"Processing first {max_files} files only")

    # Load MedCAT model
    cat = load_medcat_model()
    if cat is None:
        print("\nCannot proceed without MedCAT model.")
        return False

    # Process files
    print(f"\nProcessing {len(xml_files)} files...")
    pmid_to_concepts = {}

    # Process files sequentially (MedCAT model is not thread-safe by default)
    for xml_file in tqdm(xml_files, desc="Processing XML files"):
        try:
            file_results = process_xml_file(xml_file, cat)
            pmid_to_concepts.update(file_results)

            # Periodic save
            if len(pmid_to_concepts) % 100000 == 0:
                print(f"\n  Processed {len(pmid_to_concepts)} articles so far...")

        except Exception as e:
            print(f"\nError processing {xml_file}: {e}")
            continue

    print(f"\nIndexing complete! Processed {len(pmid_to_concepts)} articles")

    # Save the cache
    print(f"\nSaving cache to: {OUTPUT_CACHE_FILE}")
    with open(OUTPUT_CACHE_FILE, 'wb') as f:
        pickle.dump(pmid_to_concepts, f)

    print(f"Cache saved successfully! Size: {OUTPUT_CACHE_FILE.stat().st_size / 1024 / 1024:.2f} MB")

    return True


def create_sample_cache():
    """
    Create a sample cache file for testing without MedCAT.

    This creates a minimal cache with synthetic data to allow
    testing the web application without the full indexing pipeline.
    """
    print("\n" + "=" * 60)
    print("Creating Sample Cache for Testing")
    print("=" * 60)

    # Create sample data with some realistic CUIs
    sample_cuis = [
        'C0025202',  # Melanoma
        'C0011581',  # Depression
        'C0011849',  # Diabetes
        'C0007102',  # Colon Cancer
        'C0027051',  # Myocardial Infarction
        'C0007124',  # Breast Cancer
        'C0002736',  # Amyotrophic Lateral Sclerosis
        'C0004096',  # Asthma
        'C0020538',  # Hypertension
        'C0038454',  # Stroke
        'C0024117',  # COPD
        'C0022116',  # Ischemia
        'C0003873',  # Arthritis
        'C0030567',  # Parkinson Disease
        'C0002395',  # Alzheimer Disease
    ]

    # Create synthetic PMID to concepts mapping
    import random
    random.seed(42)

    sample_data = {}
    for i in range(1000):
        pmid = str(10000000 + i)
        # Each article gets 3-10 random concepts
        num_concepts = random.randint(3, 10)
        concepts = random.choices(sample_cuis, k=num_concepts)
        sample_data[pmid] = {
            'title': f'Sample Article {i}',
            'concepts': concepts
        }

    # Save the sample cache
    print(f"Creating sample cache with {len(sample_data)} entries...")
    with open(OUTPUT_CACHE_FILE, 'wb') as f:
        pickle.dump(sample_data, f)

    print(f"Sample cache saved to: {OUTPUT_CACHE_FILE}")

    # Create a sample preset file
    preset_file = SCRIPT_DIR / "presets" / "Sample - 100 PMIDs.txt"
    preset_file.parent.mkdir(parents=True, exist_ok=True)

    with open(preset_file, 'w') as f:
        for pmid in list(sample_data.keys())[:100]:
            f.write(f"{pmid}\n")

    print(f"Sample preset file saved to: {preset_file}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Index PubMed files for BioKGrapher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Download PubMed baseline files
    python index_pubmed.py --download

    # Download baseline and update files
    python index_pubmed.py --download --include-updates

    # Index downloaded files
    python index_pubmed.py --index

    # Download and index in one command
    python index_pubmed.py --download --index

    # Create sample data for testing (no MedCAT required)
    python index_pubmed.py --sample

    # Index only first 10 files (for testing)
    python index_pubmed.py --index --max-files 10
        """
    )

    parser.add_argument(
        '--download',
        action='store_true',
        help='Download PubMed XML files from NCBI'
    )
    parser.add_argument(
        '--include-updates',
        action='store_true',
        help='Also download update files (in addition to baseline)'
    )
    parser.add_argument(
        '--index',
        action='store_true',
        help='Index downloaded PubMed files using MedCAT'
    )
    parser.add_argument(
        '--sample',
        action='store_true',
        help='Create sample cache file for testing (no MedCAT required)'
    )
    parser.add_argument(
        '--max-files',
        type=int,
        default=None,
        help='Maximum number of XML files to process (for testing)'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=1,
        help='Number of parallel workers (default: 1, MedCAT is not thread-safe)'
    )

    args = parser.parse_args()

    if not any([args.download, args.index, args.sample]):
        parser.print_help()
        print("\nError: Please specify at least one action: --download, --index, or --sample")
        sys.exit(1)

    success = True

    if args.download:
        success = download_pubmed_files(include_updates=args.include_updates)

    if args.index and success:
        success = index_pubmed_files(num_workers=args.workers, max_files=args.max_files)

    if args.sample:
        success = create_sample_cache()

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
