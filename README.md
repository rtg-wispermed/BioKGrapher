# BioKGrapher

[![Affiliated with RTG WisPerMed](https://img.shields.io/badge/Affiliated-RTG%202535%20WisPerMed-blue)](https://wispermed.org/)

## Overview

BioKGrapher is a comprehensive tool designed for the automatic construction of knowledge graphs (KGs) from large-scale biomedical literature, processing PubMed IDs as input. By leveraging NLP techniques, BioKGrapher extracts and ranks biomedical concepts, integrating them into structured KGs. This tool can be valuable to construct specialized KGs to get a conceptual view on a topic of interest or to export the KG for further applications such as predictive modeling, drug repurposing, document classification, RAG and decision support systems.

## Demo

![](https://github.com/rtg-wispermed/BioKGrapher/blob/main/demo.gif)

## Key Features

- **Automatic Knowledge Graph Construction**: Extracts and integrates biomedical concepts from large PMID sets
- **Named Entity Recognition and Linking (NER+NEL)**: Utilizes [MedCAT](https://github.com/CogStack/MedCAT) for identifying and normalizing biomedical concepts using the UMLS Metathesaurus
- **Concept Weighting and Re-Ranking**: Applies Kullback-Leibler divergence and local frequency weighting to identify prevalent concepts specific to the provided set
- **Hierarchical Structuring and Relationship Mapping**: Constructs hierarchical knowledge graphs with semantic triples using UMLS's MRHIER and MRREL files
- **Interactive Visualizations**: Tree maps, sunburst charts, and classic tree views
- **Evaluation**: Evaluates constructed KGs by comparing them with concepts extracted from evidence-based clinical practice guidelines
- **Downstream Applications**: Demonstrates utility in document classification and drug repurposing tasks

## Table of Contents

1. [Quick Start](#quick-start)
2. [Project Setup](#project-setup)
3. [UMLS License Requirement](#umls-license-requirement)
4. [Prerequisites](#prerequisites)
5. [Building the Index](#building-the-index)
6. [Running the Application](#running-the-application)
7. [Usage Guide](#usage-guide)
8. [Project Structure](#project-structure)
9. [Troubleshooting](#troubleshooting)
10. [Contributing](#contributing)

## Quick Start

For a quick test with sample data (no UMLS license required):

```bash
# Clone and navigate to the project
git clone https://github.com/rtg-wispermed/BioKGrapher.git
cd BioKGrapher

# Install dependencies
pip install -r requirements.txt

# Create sample data for testing
cd src
python build_cache.py --sample
python index_pubmed.py --sample

# Run the application
python web_app.py
```

Then open your browser to `http://localhost:6006`

## Project Setup

### 1. Clone the Repository

```bash
git clone https://github.com/rtg-wispermed/BioKGrapher.git
cd BioKGrapher
```

### 2. Create a Virtual Environment (Recommended)

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Requirements

```bash
pip install -r requirements.txt
```

## UMLS License Requirement

BioKGrapher requires a valid UMLS license to access and use the UMLS Metathesaurus files for production use. Obtain a license from the [UMLS Terminology Services](https://www.nlm.nih.gov/databases/umls.html).

**Note:** You can run BioKGrapher with sample data for testing without a UMLS license using the `--sample` flags.

## Prerequisites

### 1. Download Public MedCAT Model

Once you have obtained a UMLS license, sign into your NIH profile and [download one of the following public MedCAT models](https://uts.nlm.nih.gov/uts/login?service=https://medcat.rosalind.kcl.ac.uk/auth-callback):

- **UMLS Full** (>4MM concepts trained self-supervised on MIMIC-III) - **Recommended**
- SNOMED International (Full SNOMED modelpack trained on MIMIC-III)

Unzip the model into the `models/` folder:

```bash
mkdir -p models
unzip umls_full_modelpack.zip -d models/
```

### 2. Download Required UMLS Files

[Download the Full UMLS Release Files](https://www.nlm.nih.gov/research/umls/licensedcontent/umlsknowledgesources.html) and copy the following files to `src/data/`:

- `MRCONSO.RRF` - Concept names and synonyms
- `MRHIER.RRF` - Hierarchical relationships
- `MRREL.RRF` - Semantic relationships
- `MRDEF.RRF` - Concept definitions

```bash
# Example: Copy files from UMLS download
cp /path/to/umls/META/MRCONSO.RRF src/data/
cp /path/to/umls/META/MRHIER.RRF src/data/
cp /path/to/umls/META/MRREL.RRF src/data/
cp /path/to/umls/META/MRDEF.RRF src/data/
```

**Important:** Use a UMLS Release that is the same version or newer than the one used in the MedCAT model (e.g., UMLS Release **2022AA** or newer).

### 3. Build the Definitions Cache

After placing UMLS files, build the definitions cache:

```bash
cd src
python build_cache.py --validate --build-definitions
```

## Building the Index

The index contains the mapping of PubMed articles to their extracted UMLS concepts. Building the full index requires:

1. Downloading PubMed baseline files (~350GB compressed)
2. Running MedCAT NER+NEL on all abstracts

### Download PubMed Files

```bash
# Create and navigate to the index directory
mkdir -p index/baseline
cd index/baseline

# Download PubMed baseline files (this will take several hours)
wget -nc ftp://ftp.ncbi.nlm.nih.gov/pubmed/baseline/*.xml.gz

# Optionally, download update files for the latest publications
mkdir -p ../updatefiles
cd ../updatefiles
wget -nc ftp://ftp.ncbi.nlm.nih.gov/pubmed/updatefiles/*.xml.gz
```

### Index the Files

```bash
cd src

# Index all downloaded files (this takes a long time - consider using a cluster)
python index_pubmed.py --index

# Or test with a limited number of files first
python index_pubmed.py --index --max-files 10
```

### Alternative: Use Sample Data for Testing

If you don't want to download the full PubMed corpus:

```bash
cd src
python index_pubmed.py --sample
```

This creates a minimal cache file with synthetic data for testing the web interface.

## Running the Application

After setting up the data files, start the web application:

```bash
cd src
python web_app.py
```

The application will start on `http://0.0.0.0:6006` by default. Open your browser and navigate to this URL.

### Configuration

Edit `src/config.conf` to customize settings:

```ini
[data_paths]
kld_cache_with_titles = new_full_kld_cache_with_titles.pkl
mrdef_file = data/MRDEF.RRF
definitions_file = definitions.txt
mrconso_file = data/MRCONSO.RRF
mrhier_file = data/MRHIER.RRF
preset_folder = ./presets/
frequency_output = frequencies.pkl
kld_cache_output = kld_cache.obj
tree_view_template_output = template/tree_view_template.html

[server_settings]
ip = 0.0.0.0
port = 6006
```

## Usage Guide

### 1. Select or Upload PMIDs

When you open the application, you'll be presented with options to:
- Select from preset PMID files (if any exist in `presets/` folder)
- Upload your own PMID file (one PMID per line)

### 2. Choose Target Terminology

Select the medical terminology system for the knowledge graph:
- **SNOMEDCT_US** - SNOMED Clinical Terms (US Edition)
- **NCI** - National Cancer Institute Thesaurus
- **MSH** - Medical Subject Headings (MeSH)
- **ICD10/ICD10CM/ICD10PCS** - International Classification of Diseases
- **ATC** - Anatomical Therapeutic Chemical Classification
- **FMA** - Foundational Model of Anatomy
- **LNC** - LOINC (Logical Observation Identifiers Names and Codes)

### 3. View Visualizations

The application generates three interactive visualizations:
- **Tree Map** - Hierarchical rectangle visualization showing concept importance
- **Sunburst Chart** - Radial hierarchical visualization
- **Classic Tree View** - Expandable tree structure

### Creating Preset Files

To add preset PMID collections:

1. Create a text file with one PMID per line
2. Save it in `src/presets/` with a descriptive name (e.g., `Melanoma - 128k PMIDs.txt`)
3. The preset will appear in the dropdown menu

You can obtain PMIDs from [PubMed](https://pubmed.ncbi.nlm.nih.gov/):
1. Search for your condition
2. Click "Save" → Select "All results" → Format: PMID
3. Download the file

## Project Structure

```
BioKGrapher/
├── README.md                 # This file
├── requirements.txt          # Python dependencies
├── demo.gif                  # Demo animation
├── models/                   # MedCAT model files (download required)
├── index/                    # PubMed XML files
│   ├── baseline/            # PubMed baseline files
│   └── updatefiles/         # PubMed update files
└── src/
    ├── web_app.py           # Main web application
    ├── index_pubmed.py      # PubMed indexing script
    ├── build_cache.py       # UMLS cache builder
    ├── config.conf          # Configuration file
    ├── definitions.txt      # CUI to term mappings (generated)
    ├── new_full_kld_cache_with_titles.pkl  # PMID to concepts cache (generated)
    ├── data/                # UMLS RRF files
    │   ├── MRCONSO.RRF     # Concept names
    │   ├── MRDEF.RRF       # Definitions
    │   ├── MRHIER.RRF      # Hierarchies
    │   └── MRREL.RRF       # Relationships
    ├── presets/             # Preset PMID files
    │   └── *.txt           # PMID lists
    └── template/
        └── tree_view_template.html  # HTML template for tree view
```

## Troubleshooting

### Common Issues

**1. "Missing required data files" error**

Run the setup scripts to create necessary files:
```bash
cd src
python build_cache.py --sample  # Creates sample UMLS files
python index_pubmed.py --sample  # Creates sample index
```

**2. "Configuration file not found" error**

Make sure you're running the application from the `src/` directory:
```bash
cd src
python web_app.py
```

**3. "MedCAT model not found" error**

Download and extract a MedCAT model to the `models/` folder. See [Prerequisites](#prerequisites).

**4. "No concepts found" in visualization**

- Ensure the selected terminology exists in your MRHIER.RRF file
- Try a different terminology (e.g., NCI or SNOMEDCT_US)
- Verify that the PMIDs exist in the index

**5. Memory issues when indexing**

- Process files in batches: `python index_pubmed.py --index --max-files 100`
- Use a machine with more RAM (16GB+ recommended for full indexing)

### Getting Help

- Check the [Issues](https://github.com/rtg-wispermed/BioKGrapher/issues) page
- Create a new issue with your error message and system information

## How It Works

1. **Input Processing**: Takes a list of PubMed IDs (PMIDs) as input
2. **Concept Extraction**: Uses MedCAT to perform Named Entity Recognition and Linking on article abstracts, mapping text to UMLS concepts
3. **KL Divergence Ranking**: Calculates Kullback-Leibler divergence between the user's concept distribution and the global corpus distribution to identify condition-specific concepts
4. **Hierarchy Construction**: Uses UMLS MRHIER file to build hierarchical relationships between concepts
5. **Visualization**: Generates interactive tree maps, sunburst charts, and tree views

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is affiliated with [RTG 2535 WisPerMed](https://wispermed.org/).

## Citation

If you use BioKGrapher in your research, please cite:

```bibtex
@software{biokgrapher,
  title = {BioKGrapher: Automatic Knowledge Graph Construction from Biomedical Literature},
  author = {RTG WisPerMed},
  url = {https://github.com/rtg-wispermed/BioKGrapher},
  year = {2024}
}
```

## Acknowledgments

- [MedCAT](https://github.com/CogStack/MedCAT) for NER+NEL capabilities
- [UMLS](https://www.nlm.nih.gov/research/umls/) for biomedical terminology
- [PyWebIO](https://pywebio.readthedocs.io/) for the web interface
- [Plotly](https://plotly.com/) for interactive visualizations
